from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import pluralize
from django.urls import reverse
from django.views.decorators.http import require_POST

from judicial import senhas
from judicial.selecao_documentos import documentos_lembrados
from judicial.views_processos import numero_valido, resposta_documento
from judicial.views_sistemas import ambiente_ativo, credencial_do_usuario

from . import acesso
from .models import ArquivoIngerido, LoteIngestao, PaginaExtraida
from .tasks import enfileirar

Situacao = ArquivoIngerido.Situacao


def _contagem():
    """Anotações com o total de arquivos do lote e quantos estão em cada grupo de situação."""
    return {
        'total': Count('arquivos'),
        'em_andamento': Count('arquivos', filter=Q(arquivos__situacao__in=ArquivoIngerido.EM_ANDAMENTO)),
        'reconhecendo': Count('arquivos', filter=Q(arquivos__situacao=Situacao.RECONHECENDO)),
        'processando': Count('arquivos', filter=Q(arquivos__situacao__in=ArquivoIngerido.PROCESSANDO)),
        'concluidos': Count('arquivos', filter=Q(arquivos__situacao__in=ArquivoIngerido.CONCLUIDOS)),
        'parciais': Count('arquivos', filter=Q(arquivos__situacao=Situacao.PARCIAL)),
        'sem_texto': Count('arquivos', filter=Q(arquivos__situacao=Situacao.SEM_TEXTO)),
        'aguardando_senha': Count('arquivos', filter=Q(arquivos__situacao=Situacao.AGUARDANDO_SENHA)),
        'com_erro': Count('arquivos', filter=Q(arquivos__situacao=Situacao.ERRO)),
    }


def _lotes_do_usuario(request):
    return LoteIngestao.objects.filter(usuario=request.user).select_related('ambiente__sistema').annotate(**_contagem())


def _lote_do_usuario(request, pk):
    return get_object_or_404(_lotes_do_usuario(request), pk=pk)


def _arquivos(n):
    return f'{n} arquivo{pluralize(n)}'


@require_POST
def ingerir(request, sistema_pk, pk, numero):
    """Cria um lote com os documentos marcados na tabela de eventos e o põe na fila."""
    ambiente = ambiente_ativo(sistema_pk, pk)
    numero = numero_valido(numero)
    sistema = ambiente.sistema
    pagina_processo = reverse('sistemas:processo', args=[sistema.pk, ambiente.pk, numero])
    credencial = credencial_do_usuario(request, sistema)
    senha_hash = senhas.obter(request, sistema)
    if credencial is None or senha_hash is None:
        messages.warning(request, f'Informe sua senha do {sistema.nome} para ingerir os arquivos.')
        return redirect(pagina_processo)

    exibidos = documentos_lembrados(request.user, ambiente, numero)
    if exibidos is None:
        messages.warning(request, 'A lista de documentos expirou. Confira a seleção e tente de novo.')
        return redirect(pagina_processo)
    # Só ids que estavam na tabela exibida ao usuário; sem repetição, na ordem da tabela.
    ids = [i for i in dict.fromkeys(request.POST.getlist('documentos')) if i in exibidos]
    if not ids:
        messages.warning(request, 'Marque ao menos um arquivo para ingerir.')
        return redirect(pagina_processo)

    with transaction.atomic():
        lote = LoteIngestao.objects.create(
            usuario=request.user, ambiente=ambiente, numero_processo=numero, login=credencial.login,
        )
        arquivos = ArquivoIngerido.objects.bulk_create([
            ArquivoIngerido(lote=lote, id_documento=i, nome=nome[:255], descricao=descricao[:255], tipo_local=tipo[:20],
                            evento=evento)
            for i in ids
            for nome, descricao, tipo, evento in [exibidos[i]]
        ])
    acesso.guardar(lote, senha_hash)
    enfileirar(lote, [a.pk for a in arquivos])
    messages.success(request, f'{_arquivos(len(ids))} na fila de ingestão.')
    return redirect('ingestao:lote', pk=lote.pk)


def lotes(request):
    return render(request, 'ingestao/lotes.html', {
        'lotes': _lotes_do_usuario(request),
    })


def lote(request, pk):
    lote = _lote_do_usuario(request, pk)
    sistema = lote.ambiente.sistema
    return render(request, 'ingestao/lote.html', {
        'lote': lote,
        'sistema': sistema,
        # Páginas por arquivo e quantas precisam de OCR, numa só consulta (um lote pode ter milhares de arquivos).
        'arquivos': lote.arquivos.annotate(
            total_paginas=Count('paginas'),
            paginas_ocr=Count('paginas', filter=Q(paginas__situacao__in=PaginaExtraida.PRECISAM_OCR)),
        ),
        'senha_ok': senhas.obter(request, sistema) is not None,
        'credencial': credencial_do_usuario(request, sistema),
    })


def lote_andamento(request, pk):
    """Fragmento com o resumo do lote, consultado periodicamente (htmx) enquanto há arquivos na fila ou no OCR."""
    lote = _lote_do_usuario(request, pk)
    resposta = render(request, 'ingestao/_andamento.html', {'lote': lote})
    if not lote.processando:
        resposta['HX-Refresh'] = 'true'  # terminou: recarrega a página para mostrar cada arquivo
    return resposta


@require_POST
def retomar(request, pk):
    """Põe de volta na fila os arquivos que aguardam senha ou falharam, com a senha atual."""
    lote = _lote_do_usuario(request, pk)
    senha_hash = senhas.obter(request, lote.ambiente.sistema)
    if senha_hash is None:
        messages.warning(request, f'Informe sua senha do {lote.ambiente.sistema.nome} para retomar o lote.')
        return redirect('ingestao:lote', pk=lote.pk)
    acesso.guardar(lote, senha_hash)
    retomados = list(lote.arquivos.filter(situacao__in=ArquivoIngerido.RETOMAVEIS).values_list('pk', flat=True))
    ArquivoIngerido.objects.filter(pk__in=retomados).update(situacao=Situacao.PENDENTE, mensagem='')
    enfileirar(lote, retomados)
    messages.success(request, f'{_arquivos(len(retomados))} de volta na fila.')
    return redirect('ingestao:lote', pk=lote.pk)


def _arquivo_do_usuario(request, pk):
    return get_object_or_404(ArquivoIngerido.objects.select_related('lote'), pk=pk, lote__usuario=request.user)


def original(request, pk):
    arquivo = _arquivo_do_usuario(request, pk)
    if not arquivo.arquivo:
        raise Http404('O arquivo ainda não foi baixado.')
    with arquivo.arquivo.open('rb') as f:
        dados = f.read()
    return resposta_documento(request, dados, arquivo.mimetype, '', arquivo.rotulo)


def texto(request, pk):
    arquivo = _arquivo_do_usuario(request, pk)
    return resposta_documento(request, arquivo.texto.encode(), 'text/plain', 'utf-8', arquivo.rotulo)
