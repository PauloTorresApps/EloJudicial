import mimetypes
import re
from urllib.parse import urlsplit

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from . import mni, senhas
from .models import Ambiente
from .preferencias import ambiente_padrao
from .views_sistemas import SESSAO_AMBIENTE, _ambiente_ativo, _credencial


SESSAO_BUSCA = 'busca_processo'
# Paginação da tabela de eventos (feita no navegador; ver static/judicial/eventos.js).
EVENTOS_POR_PAGINA = [(10, '10'), (25, '25'), (35, '35'), (50, '50'), (100, '100'), (0, 'Tudo')]
EVENTOS_POR_PAGINA_PADRAO = 35


def _ambiente_de_trabalho(request):
    """Ambiente em uso na sessão; se não houver, o ambiente padrão do usuário."""
    ambiente = (
        Ambiente.objects.select_related('sistema')
        .filter(pk=request.session.get(SESSAO_AMBIENTE), ativo=True, sistema__ativo=True)
        .first()
    )
    return ambiente or ambiente_padrao(request.user)


def buscar(request):
    """Recebe o número digitado na barra do topo e abre a consulta no ambiente de trabalho."""
    referer = request.META.get('HTTP_REFERER', '')
    if url_has_allowed_host_and_scheme(referer, allowed_hosts={request.get_host()}):
        partes = urlsplit(referer)
        voltar = partes.path + (f'?{partes.query}' if partes.query else '')
    else:
        voltar = reverse('inicio')
    try:
        numero = mni.normalizar_numero_processo(request.GET.get('numero', ''))
    except mni.NumeroInvalido as exc:
        messages.error(request, str(exc))
        # Mantém o que foi digitado no campo da barra, para o usuário corrigir.
        request.session[SESSAO_BUSCA] = request.GET.get('numero', '')[:25]
        return redirect(voltar)

    ambiente = _ambiente_de_trabalho(request)
    if ambiente is None:
        messages.warning(request, 'Escolha o sistema e o ambiente de trabalho para consultar o processo.')
        return redirect('sistemas:lista')
    return redirect('sistemas:processo', sistema_pk=ambiente.sistema_id, pk=ambiente.pk, numero=numero)


def _numero_valido(numero):
    try:
        return mni.normalizar_numero_processo(numero)
    except mni.NumeroInvalido as exc:
        raise Http404(str(exc)) from exc


def processo(request, sistema_pk, pk, numero):
    ambiente = _ambiente_ativo(sistema_pk, pk)
    numero = _numero_valido(numero)
    request.session[SESSAO_AMBIENTE] = ambiente.pk
    return render(request, 'judicial/processos/processo.html', {
        'ambiente': ambiente,
        'sistema': ambiente.sistema,
        'numero': numero,
        'numero_formatado': mni.formatar_numero_processo(numero),
        'credencial': _credencial(request, ambiente.sistema),
        'senha_ok': senhas.obter(request, ambiente.sistema) is not None,
    })


def processo_dados(request, sistema_pk, pk, numero):
    """Fragmento HTML com o resultado da consulta do processo (MNI consultarProcesso)."""
    ambiente = _ambiente_ativo(sistema_pk, pk)
    numero = _numero_valido(numero)
    sistema = ambiente.sistema
    credencial = _credencial(request, sistema)
    senha_hash = senhas.obter(request, sistema)
    contexto = {
        'ambiente': ambiente, 'sistema': sistema, 'credencial': credencial,
        'numero': numero, 'numero_formatado': mni.formatar_numero_processo(numero),
        'voltar_para': reverse('sistemas:processo', args=[sistema.pk, ambiente.pk, numero]),
        'opcoes_por_pagina': EVENTOS_POR_PAGINA,
        'por_pagina_padrao': EVENTOS_POR_PAGINA_PADRAO,
    }
    if credencial is None or senha_hash is None:
        contexto['pedir_senha'] = True
        return render(request, 'judicial/processos/_processo.html', contexto)

    try:
        resultado = mni.consultar_processo(ambiente, credencial.login, senha_hash, numero)
    except mni.ErroMNI as exc:
        contexto['erro'] = str(exc)
    else:
        if resultado.processo is not None:
            if not resultado.processo.numero:
                resultado.processo.numero = numero
            mni.descrever_competencia(ambiente, resultado.processo)
        if mni.falha_de_autenticacao(resultado):
            senhas.esquecer(request, sistema)
            contexto['pedir_senha'] = True
            contexto['erro_senha'] = ' '.join(resultado.mensagens)
        contexto['resultado'] = resultado
    return render(request, 'judicial/processos/_processo.html', contexto)


# Tipos exibidos no navegador; os demais são sempre baixados.
TIPOS_EXIBIVEIS = {'application/pdf', 'text/html', 'text/plain', 'image/png', 'image/jpeg', 'image/gif'}


def _nome_arquivo(nome, mimetype):
    base = re.sub(r'[^\w.-]+', '_', nome or '', flags=re.ASCII).strip('._')[:80] or 'documento'
    extensao = mimetypes.guess_extension(mimetype) or ''
    if extensao == '.htm':
        extensao = '.html'
    return base if base.lower().endswith(extensao) else base + extensao


def documento(request, sistema_pk, pk, numero, id_documento):
    """Entrega o arquivo de um documento do processo (MNI consultarDocumentosProcesso)."""
    ambiente = _ambiente_ativo(sistema_pk, pk)
    numero = _numero_valido(numero)
    sistema = ambiente.sistema
    pagina_processo = reverse('sistemas:processo', args=[sistema.pk, ambiente.pk, numero])
    credencial = _credencial(request, sistema)
    senha_hash = senhas.obter(request, sistema)
    if credencial is None or senha_hash is None:
        messages.warning(request, f'Informe sua senha do {sistema.nome} para abrir o documento.')
        return redirect(pagina_processo)

    try:
        resultado = mni.consultar_documento(ambiente, credencial.login, senha_hash, numero, id_documento)
    except mni.ErroMNI as exc:
        messages.error(request, f'Não foi possível abrir o documento: {exc}')
        return redirect(pagina_processo)
    if mni.falha_de_autenticacao(resultado):
        senhas.esquecer(request, sistema)
        messages.error(request, 'O tribunal recusou a senha. Informe-a novamente para abrir o documento.')
        return redirect(pagina_processo)
    doc = next((d for d in resultado.documentos if d.id == id_documento), None) or (resultado.documentos or [None])[0]
    if not resultado.sucesso or doc is None or not doc.dados:
        motivo = ' '.join(resultado.mensagens) or 'o tribunal não enviou o conteúdo.'
        messages.error(request, f'Não foi possível abrir o documento: {motivo}')
        return redirect(pagina_processo)

    mimetype = doc.mimetype.split(';')[0].strip().lower() or 'application/octet-stream'
    exibir = mimetype in TIPOS_EXIBIVEIS and 'baixar' not in request.GET
    tipo_resposta = mimetype
    if mimetype.startswith('text/'):
        tipo_resposta = f'{mimetype}; charset={doc.encoding or "utf-8"}'
    resposta = HttpResponse(doc.dados, content_type=tipo_resposta)
    nome = _nome_arquivo(request.GET.get('nome', id_documento), mimetype)
    resposta['Content-Disposition'] = f'{"inline" if exibir else "attachment"}; filename="{nome}"'
    resposta['X-Content-Type-Options'] = 'nosniff'
    resposta['Cache-Control'] = 'private, no-store'
    resposta['Referrer-Policy'] = 'no-referrer'
    if mimetype != 'application/pdf':
        # Documentos (sobretudo HTML) vêm de fora: isola a página e bloqueia scripts.
        resposta['Content-Security-Policy'] = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"
    return resposta
