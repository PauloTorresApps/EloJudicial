"""Tarefas da ingestão (filas "ingestao" e "ocr" do Celery; ver config/celery.py).

Uma tarefa por lote e uma requisição ao tribunal para todos os documentos dele (menos
chamadas, para o IP não ser bloqueado). A mensagem leva só os ids do lote e dos arquivos.
A senha do tribunal é lida do cache sem persistência (ingestao/acesso.py) na hora de
executar; o conteúdo vai para o armazenamento "ingestao".

As páginas sem texto aproveitável seguem, uma tarefa por arquivo, para a fila "ocr"
(worker-ocr; docs/0018 e 0019), que lê o original guardado e não chama o tribunal.
"""

import functools
import hashlib
import logging
import mimetypes

from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction

from judicial import limitador, mni

from . import acesso
import ocr  # leve: as dependências pesadas só são importadas ao ler (e só existem na imagem "ocr")

from .extracao import Extracao, classificar_leitura, extrair_texto, qualidade_do_texto
from .models import ArquivoIngerido, PaginaExtraida

logger = logging.getLogger(__name__)
Situacao = ArquivoIngerido.Situacao

TENTATIVAS = 3  # falhas de comunicação com o tribunal: tenta de novo, com espera crescente
ESPERA_TRIBUNAL = 3 * 60  # segundos pela vez do tribunal (judicial.limitador); depois, nova tentativa
# Abaixo desta confiança, a leitura do Tesseract ganha uma segunda, com o RapidOCR (docs/0019). Provisório.
CONFIANCA_MINIMA_OCR = 0.85
# Páginas lidas por tarefa de OCR; o resto do arquivo segue numa nova tarefa, no fim da fila (docs/0021).
# 50 páginas levam ~5 min no pior caso (~5,5 s cada, docs/0019), longe do visibility_timeout de 2 h.
PAGINAS_POR_TAREFA = 50


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
    # Também no `reextrair`: as páginas são regravadas com o texto nativo e voltam ao OCR.
    vai_para_ocr = settings.INGESTAO_OCR and bool(extracao.precisam_ocr)
    with transaction.atomic():
        arquivo.paginas.all().delete()
        PaginaExtraida.objects.bulk_create([
            PaginaExtraida(arquivo=arquivo, numero=p.numero, situacao=p.situacao, texto=p.texto,
                           caracteres=p.caracteres, qualidade=p.qualidade, maior_imagem=p.maior_imagem)
            for p in extracao.paginas
        ], batch_size=500)  # PDFs de milhares de páginas: evita um único INSERT gigante
        arquivo.situacao, arquivo.mensagem = situacao_do_arquivo(extracao, vai_para_ocr)
        arquivo.save(update_fields=['situacao', 'mensagem', 'atualizado_em'])
        if vai_para_ocr:
            transaction.on_commit(functools.partial(reconhecer_arquivo.delay, arquivo.pk))


# soft_time_limit: proteção, bem abaixo do visibility_timeout; levanta a exceção na tarefa e o finally roda.
@shared_task(name='ingestao.reconhecer_arquivo', soft_time_limit=30 * 60, time_limit=35 * 60)
def reconhecer_arquivo(arquivo_id):
    """OCR das páginas do arquivo que precisam dele (docs/0018 e 0019), até PAGINAS_POR_TAREFA por vez.

    Sobrando páginas, a tarefa se põe de novo no fim da fila (docs/0021): nenhuma passa do
    visibility_timeout, e um arquivo enorme não segura um worker enquanto outros esperam.

    Idempotente: a página lida sai de PRECISAM_OCR (com texto, em branco ou ilegível) e não é lida de
    novo. Cada página é gravada assim que lida, então uma queda do worker no meio não perde o que já
    foi feito. Ao terminar, e também se o OCR falhar, o arquivo sai de "reconhecendo": a situação é
    recalculada pelas páginas (as que não foram lidas continuam marcadas para OCR).
    """
    arquivo = ArquivoIngerido.objects.select_related('lote').filter(pk=arquivo_id).exclude(arquivo='').first()
    if arquivo is None:
        return
    continua = False
    try:
        continua = _reconhecer(arquivo, PAGINAS_POR_TAREFA)
    finally:
        # PaginaExtraida tem o que a regra do arquivo usa (número e situação).
        arquivo.situacao, arquivo.mensagem = situacao_do_arquivo(
            Extracao(list(arquivo.paginas.only('numero', 'situacao').order_by('numero'))), vai_para_ocr=continua)
        arquivo.save(update_fields=['situacao', 'mensagem', 'atualizado_em'])
    if continua:
        reconhecer_arquivo.delay(arquivo.pk)
        logger.info('Lote %s: OCR do arquivo %s continua noutra tarefa (%s)', arquivo.lote_id, arquivo.pk, arquivo.mensagem)
        return
    logger.info('Lote %s: OCR do arquivo %s concluído (%s)', arquivo.lote_id, arquivo.pk, arquivo.get_situacao_display())


def _reconhecer(arquivo, limite):
    """Lê até `limite` páginas pendentes do arquivo; devolve se ainda sobram páginas para outra tarefa."""
    pendentes = {p.numero: p for p in arquivo.paginas.filter(situacao__in=PaginaExtraida.PRECISAM_OCR)
                 .only('pk', 'numero', 'situacao')}
    if not pendentes:
        return False

    # Mesmo documento já reconhecido em outro lote do usuário (mesmo SHA-256): reaproveita o texto.
    for copia in PaginaExtraida.objects.filter(
        arquivo__sha256=arquivo.sha256, arquivo__lote__usuario_id=arquivo.lote.usuario_id,
        numero__in=pendentes,
    ).exclude(arquivo=arquivo).exclude(metodo=PaginaExtraida.Metodo.NATIVO).order_by('numero', '-pk').only(
        'numero', 'texto', 'metodo', 'confianca',
    ):
        if copia.numero in pendentes:
            _gravar_ocr(pendentes.pop(copia.numero), copia.texto, copia.metodo, copia.confianca)

    agora = sorted(pendentes)[:limite]  # o reaproveitamento acima não conta: não roda o motor
    if agora:
        # Pelo caminho no volume: o pdfium lê sob demanda, sem o PDF inteiro na memória.
        for numero, imagem in ocr.paginas(arquivo.arquivo.path, arquivo.mimetype, agora):
            leitura = ler_pagina(imagem)
            _gravar_ocr(pendentes[numero], leitura.texto, f'ocr_{leitura.motor}', leitura.confianca)
    return len(pendentes) > len(agora)


def ler_pagina(imagem):
    """Tesseract; abaixo da confiança mínima, também o RapidOCR, e fica a leitura de melhor qualidade."""
    leitura = ocr.tesseract(imagem)
    if leitura.confianca >= CONFIANCA_MINIMA_OCR:
        return leitura
    return max(leitura, ocr.rapid(imagem), key=lambda lida: qualidade_do_texto(lida.texto))


def _gravar_ocr(pagina, texto, metodo, confianca):
    """Troca o texto nativo pelo do OCR e reclassifica a página por ele."""
    lida = classificar_leitura(pagina.numero, texto)
    pagina.texto, pagina.caracteres, pagina.qualidade, pagina.situacao = lida.texto, lida.caracteres, lida.qualidade, lida.situacao
    pagina.metodo, pagina.confianca = metodo, confianca
    pagina.save(update_fields=['texto', 'caracteres', 'qualidade', 'situacao', 'metodo', 'confianca'])


def situacao_do_arquivo(extracao, vai_para_ocr=False):
    """(situação, mensagem) do arquivo a partir das páginas: as que ainda precisam de OCR e, depois
    dele, as que ficaram ilegíveis. Com `vai_para_ocr`, as que precisam já estão na fila do OCR."""
    if not extracao.paginas:
        return Situacao.SEM_TEXTO, extracao.aviso
    if vai_para_ocr and (pendentes := [p.numero for p in extracao.precisam_ocr]):
        return Situacao.RECONHECENDO, f'{len(pendentes)} de {len(extracao.paginas)} páginas na fila do OCR ({_paginas(pendentes)}).'
    if pendentes := [p.numero for p in extracao.precisam_ocr]:
        sem_texto, parcial = 'Sem texto aproveitável: precisa de OCR', 'precisam de OCR'
    elif pendentes := [p.numero for p in extracao.ilegiveis]:
        sem_texto, parcial = 'Sem texto aproveitável mesmo com OCR', 'ilegíveis mesmo com OCR'
    else:
        return (Situacao.PRONTO, '') if extracao.tem_texto else (Situacao.SEM_TEXTO, 'O documento está em branco.')
    if not extracao.tem_texto:
        return Situacao.SEM_TEXTO, f'{sem_texto} ({_paginas(pendentes)}).'
    return Situacao.PARCIAL, f'{len(pendentes)} de {len(extracao.paginas)} páginas {parcial} ({_paginas(pendentes)}).'


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
