"""Tarefas da ingestão (fila "ingestao" do Celery; ver config/celery.py).

Uma tarefa por lote e uma requisição ao tribunal para todos os documentos dele (menos
chamadas, para o IP não ser bloqueado). A mensagem leva só os ids do lote e dos arquivos.
A senha do tribunal é lida do cache sem persistência (ingestao/acesso.py) na hora de
executar; o conteúdo vai para o armazenamento "ingestao".
"""

import hashlib
import logging
import mimetypes

from celery import shared_task
from django.core.files.base import ContentFile
from django.db import transaction

from judicial import limitador, mni

from . import acesso
from .extracao import extrair_texto
from .models import ArquivoIngerido, PaginaExtraida

logger = logging.getLogger(__name__)
Situacao = ArquivoIngerido.Situacao

TENTATIVAS = 3  # falhas de comunicação com o tribunal: tenta de novo, com espera crescente
ESPERA_TRIBUNAL = 3 * 60  # segundos pela vez do tribunal (judicial.limitador); depois, nova tentativa


def enfileirar(lote, arquivos_ids):
    """Põe os arquivos do lote na fila depois do commit (o worker precisa enxergar os registros).

    Uma única tarefa para o lote: todos os documentos vão no mesmo envelope ao tribunal.
    """
    lote_id, ids = lote.pk, list(arquivos_ids)
    if ids:
        transaction.on_commit(lambda: ingerir_lote.delay(lote_id, ids))


@shared_task(bind=True, name='ingestao.ingerir_lote', max_retries=TENTATIVAS)
def ingerir_lote(self, lote_id, arquivos_ids):
    """Baixa do tribunal (MNI), numa só requisição, os documentos dos arquivos; guarda os originais e extrai o texto."""
    # Idempotente: só pega os arquivos ainda em andamento (a mensagem pode ser entregue mais de uma vez).
    # BAIXANDO entra para retomar após queda do worker.
    with transaction.atomic():
        arquivos = list(ArquivoIngerido.objects.select_for_update(of=('self',)).select_related('lote__ambiente__sistema')
                        .filter(lote_id=lote_id, pk__in=arquivos_ids, situacao__in=ArquivoIngerido.EM_ANDAMENTO)
                        .order_by('pk'))
        _marcar(arquivos, Situacao.BAIXANDO, '')
    if not arquivos:
        return
    lote = arquivos[0].lote

    senha_hash = acesso.obter(lote)
    if senha_hash is None:
        _marcar(arquivos, Situacao.AGUARDANDO_SENHA,
                'A senha do sistema expirou ou não foi informada. Informe-a e retome o lote.')
        return

    with limitador.paciencia(ESPERA_TRIBUNAL):  # em segundo plano pode esperar mais pela vez do tribunal
        restantes, exc = _baixar(lote, senha_hash, arquivos)
    if not restantes:
        return
    if self.request.retries < self.max_retries:
        _marcar(restantes, Situacao.PENDENTE, f'Nova tentativa: {exc}')
        raise self.retry(args=[lote_id, [a.pk for a in restantes]], exc=exc, countdown=30 * 2 ** self.request.retries)
    _marcar(restantes, Situacao.ERRO, str(exc))


@shared_task(name='ingestao.ingerir_arquivo')
def ingerir_arquivo(arquivo_id):
    """Compatibilidade: mensagens antigas (um arquivo por tarefa) ainda na fila. Remover quando ela esvaziar."""
    lote_id = ArquivoIngerido.objects.filter(pk=arquivo_id).values_list('lote_id', flat=True).first()
    if lote_id is not None:
        ingerir_lote.delay(lote_id, [arquivo_id])


def _baixar(lote, senha_hash, arquivos):
    """Uma requisição para todos os arquivos; se a resposta passar do tamanho máximo, o grupo é dividido ao meio.

    Devolve (arquivos que faltaram, erro de comunicação) — ([], None) quando não há o que repetir.
    """
    grupos = [arquivos]
    while grupos:
        grupo = grupos.pop(0)
        ids = list(dict.fromkeys(a.id_documento for a in grupo))
        try:
            resultado = mni.consultar_documentos(lote.ambiente, lote.login, senha_hash, lote.numero_processo, ids)
        except mni.RespostaGrandeDemais as exc:
            if len(grupo) == 1:
                _marcar(grupo, Situacao.ERRO, str(exc))
                continue
            meio = len(grupo) // 2
            logger.warning('Lote %s: resposta grande demais para %d documentos; dividindo em dois pedidos',
                           lote.pk, len(grupo))
            grupos[:0] = [grupo[:meio], grupo[meio:]]
            continue
        except mni.ErroMNI as exc:
            return [a for g in [grupo, *grupos] for a in g], exc

        if mni.falha_de_autenticacao(resultado):
            # Senha recusada: para o lote inteiro (sem novos pedidos) até o usuário informá-la de novo.
            acesso.esquecer(lote)
            lote.arquivos.filter(situacao__in=ArquivoIngerido.EM_ANDAMENTO).update(
                situacao=Situacao.AGUARDANDO_SENHA, mensagem='O tribunal recusou a senha. Informe-a de novo e retome o lote.',
            )
            return [], None
        _guardar_grupo(grupo, ids, resultado)
    return [], None


def _guardar_grupo(grupo, ids, resultado):
    # Cada conteúdo fica só em `recebidos` e é liberado assim que guardado (o lote não repete ids: ver views.ingerir).
    recebidos = resultado.por_id(ids) if resultado.sucesso else {}
    resultado.documentos.clear()
    sem_conteudo = []
    for arquivo in grupo:
        doc = recebidos.pop(arquivo.id_documento, None)
        if doc is None:
            sem_conteudo.append(arquivo)
        else:
            _guardar(arquivo, doc)
    _marcar(sem_conteudo, Situacao.ERRO, ' '.join(resultado.mensagens) or 'O tribunal não enviou o conteúdo.')


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


def _marcar(arquivos, situacao, mensagem):
    if arquivos:
        ArquivoIngerido.objects.filter(pk__in=[a.pk for a in arquivos]).update(situacao=situacao, mensagem=mensagem)
