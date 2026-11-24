"""Tarefas da ingestão (fila "ingestao" do Celery; ver config/celery.py).

A mensagem leva só o id do arquivo. A senha do tribunal é lida do cache sem persistência
(ingestao/acesso.py) na hora de executar; o conteúdo vai para o armazenamento "ingestao".
"""

import hashlib
import logging
import mimetypes

from celery import shared_task
from django.core.files.base import ContentFile
from django.db import transaction

from judicial import mni

from . import acesso
from .extracao import extrair_texto
from .models import ArquivoIngerido, PaginaExtraida

logger = logging.getLogger(__name__)
Situacao = ArquivoIngerido.Situacao

TENTATIVAS = 3  # falhas de comunicação com o tribunal: tenta de novo, com espera crescente


def enfileirar(arquivos_ids):
    """Põe os arquivos na fila depois do commit (o worker precisa enxergar os registros)."""
    ids = list(arquivos_ids)
    transaction.on_commit(lambda: [ingerir_arquivo.delay(pk) for pk in ids])


@shared_task(bind=True, name='ingestao.ingerir_arquivo', max_retries=TENTATIVAS)
def ingerir_arquivo(self, arquivo_id):
    """Baixa um documento do tribunal (MNI), guarda o original e extrai o texto."""
    # Idempotente: arquivo concluído, com erro ou aguardando senha não é processado de novo
    # (a mensagem pode ser entregue mais de uma vez). BAIXANDO entra para retomar após queda do worker.
    if not ArquivoIngerido.objects.filter(pk=arquivo_id, situacao__in=ArquivoIngerido.EM_ANDAMENTO).update(
        situacao=Situacao.BAIXANDO, mensagem='',
    ):
        return
    arquivo = ArquivoIngerido.objects.select_related('lote__ambiente__sistema').get(pk=arquivo_id)
    lote = arquivo.lote

    senha_hash = acesso.obter(lote)
    if senha_hash is None:
        _marcar(arquivo, Situacao.AGUARDANDO_SENHA, 'A senha do sistema expirou ou não foi informada. Informe-a e retome o lote.')
        return

    try:
        resultado = mni.consultar_documento(lote.ambiente, lote.login, senha_hash, lote.numero_processo, arquivo.id_documento)
    except mni.RespostaGrandeDemais as exc:
        _marcar(arquivo, Situacao.ERRO, str(exc))
        return
    except mni.ErroMNI as exc:
        if self.request.retries < self.max_retries:
            _marcar(arquivo, Situacao.PENDENTE, f'Nova tentativa: {exc}')
            raise self.retry(exc=exc, countdown=30 * 2 ** self.request.retries) from exc
        _marcar(arquivo, Situacao.ERRO, str(exc))
        return

    if mni.falha_de_autenticacao(resultado):
        # Senha recusada: para o lote inteiro até o usuário informá-la de novo.
        acesso.esquecer(lote)
        lote.arquivos.filter(situacao__in=ArquivoIngerido.EM_ANDAMENTO).update(
            situacao=Situacao.AGUARDANDO_SENHA, mensagem='O tribunal recusou a senha. Informe-a de novo e retome o lote.',
        )
        return

    doc = resultado.documento(arquivo.id_documento)
    if not resultado.sucesso or doc is None or not doc.dados:
        _marcar(arquivo, Situacao.ERRO, ' '.join(resultado.mensagens) or 'O tribunal não enviou o conteúdo.')
        return

    _guardar(arquivo, doc)


def _guardar(arquivo, doc):
    if arquivo.arquivo:  # reprocessamento: substitui o original anterior
        arquivo.arquivo.delete(save=False)
    mimetype = doc.mimetype_base
    extensao = mimetypes.guess_extension(mimetype) or ''
    arquivo.arquivo.save(f'{arquivo.pk}{extensao}', ContentFile(doc.dados), save=False)
    arquivo.mimetype = mimetype
    arquivo.codificacao = doc.encoding[:50]
    arquivo.tamanho = len(doc.dados)
    arquivo.sha256 = hashlib.sha256(doc.dados).hexdigest()
    arquivo.save()
    extrair_e_salvar(arquivo, doc.dados)
    logger.info('Lote %s: arquivo %s ingerido (%s, %d bytes, %s)',
                arquivo.lote_id, arquivo.pk, mimetype, arquivo.tamanho, arquivo.get_situacao_display())


def extrair_e_salvar(arquivo, dados):
    """Extrai o texto de `dados` página a página e grava as páginas e a situação do arquivo.

    Usado na ingestão e no reprocessamento dos originais já guardados (comando `reextrair`).
    """
    extracao = extrair_texto(dados, arquivo.mimetype, arquivo.codificacao)
    with transaction.atomic():
        arquivo.paginas.all().delete()
        PaginaExtraida.objects.bulk_create([
            PaginaExtraida(arquivo=arquivo, numero=p.numero, situacao=p.situacao, texto=p.texto,
                           caracteres=p.caracteres, qualidade=p.qualidade, maior_imagem=p.maior_imagem)
            for p in extracao.paginas
        ], batch_size=500)  # PDFs de milhares de páginas: evita um único INSERT gigante
        arquivo.situacao, arquivo.mensagem = situacao_do_arquivo(extracao)
        arquivo.save(update_fields=['situacao', 'mensagem', 'atualizado_em'])


def situacao_do_arquivo(extracao):
    """(situação, mensagem) do arquivo a partir das páginas extraídas."""
    if not extracao.paginas:
        return Situacao.SEM_TEXTO, extracao.aviso
    pendentes = [p.numero for p in extracao.precisam_ocr]
    if not pendentes:
        return (Situacao.PRONTO, '') if extracao.tem_texto else (Situacao.SEM_TEXTO, 'O documento está em branco.')
    if not extracao.tem_texto:
        return Situacao.SEM_TEXTO, f'Sem texto aproveitável: precisa de OCR ({_paginas(pendentes)}).'
    return Situacao.PARCIAL, f'{len(pendentes)} de {len(extracao.paginas)} páginas precisam de OCR ({_paginas(pendentes)}).'


def _paginas(numeros):
    """[1, 2, 3, 5] -> 'páginas 1–3, 5'; uma só -> 'página 4'."""
    faixas, inicio = [], numeros[0]
    for anterior, atual in zip(numeros, numeros[1:] + [None]):
        if atual != anterior + 1:
            faixas.append(str(inicio) if inicio == anterior else f'{inicio}–{anterior}')
            inicio = atual
    rotulo = 'página' if len(numeros) == 1 else 'páginas'
    return f'{rotulo} {", ".join(faixas[:10])}{", …" if len(faixas) > 10 else ""}'


def _marcar(arquivo, situacao, mensagem):
    ArquivoIngerido.objects.filter(pk=arquivo.pk).update(situacao=situacao, mensagem=mensagem)
