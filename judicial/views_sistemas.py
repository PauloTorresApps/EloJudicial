from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import mni, senhas
from .models import Ambiente, Credencial, SistemaJudicial
from .preferencias import ambiente_padrao, definir_ambiente_padrao

SESSAO_AMBIENTE = 'ambiente_id'


def _destino_seguro(request, destino):
    if destino and url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}):
        return destino
    return None


def _credencial(request, sistema):
    return request.user.credenciais_judiciais.filter(sistema=sistema).first()


def lista(request):
    sistemas = SistemaJudicial.objects.filter(ativo=True).annotate(
        total_ambientes=Count('ambientes', filter=Q(ambientes__ativo=True))
    ).order_by('nome')  # Meta.ordering é ignorado em consultas com agregação
    logins = dict(request.user.credenciais_judiciais.values_list('sistema_id', 'login'))
    for sistema in sistemas:
        sistema.login_usuario = logins.get(sistema.pk, '')
    return render(request, 'judicial/sistemas/lista.html', {
        'sistemas': sistemas,
        'desbloqueados': senhas.sistemas_desbloqueados(request),
        'padrao': ambiente_padrao(request.user),
    })


def ambientes(request, pk):
    sistema = get_object_or_404(SistemaJudicial, pk=pk, ativo=True)
    return render(request, 'judicial/sistemas/ambientes.html', {
        'sistema': sistema,
        'ambientes': sistema.ambientes.filter(ativo=True),
        'credencial': _credencial(request, sistema),
        'senha_ok': senhas.obter(request, sistema) is not None,
        'ambiente_atual_id': request.session.get(SESSAO_AMBIENTE),
        'padrao': ambiente_padrao(request.user),
    })


def _ambiente_ativo(sistema_pk, pk):
    return get_object_or_404(
        Ambiente.objects.select_related('sistema'),
        pk=pk, sistema_id=sistema_pk, ativo=True, sistema__ativo=True,
    )


def servicos(request, sistema_pk, pk):
    """Área de trabalho do ambiente. Os avisos pendentes são carregados em seguida (htmx)."""
    ambiente = _ambiente_ativo(sistema_pk, pk)
    # O ambiente escolhido fica guardado na sessão: é ele que o EloJudicial usa nos serviços.
    request.session[SESSAO_AMBIENTE] = ambiente.pk
    return render(request, 'judicial/sistemas/servicos.html', {
        'ambiente': ambiente,
        'sistema': ambiente.sistema,
        'credencial': _credencial(request, ambiente.sistema),
        'senha_ok': senhas.obter(request, ambiente.sistema) is not None,
        'padrao': ambiente_padrao(request.user),
    })


def avisos_pendentes(request, sistema_pk, pk):
    """Fragmento HTML com o resultado da consulta de avisos pendentes (MNI)."""
    ambiente = _ambiente_ativo(sistema_pk, pk)
    sistema = ambiente.sistema
    credencial = _credencial(request, sistema)
    senha_hash = senhas.obter(request, sistema)
    contexto = {
        'ambiente': ambiente, 'sistema': sistema, 'credencial': credencial,
        'voltar_para': reverse('sistemas:servicos', args=[sistema.pk, ambiente.pk]),
    }
    if credencial is None or senha_hash is None:
        contexto['pedir_senha'] = True
        return render(request, 'judicial/sistemas/_avisos.html', contexto)

    try:
        resultado = mni.consultar_avisos_pendentes(ambiente, credencial.login, senha_hash)
    except mni.ErroMNI as exc:
        contexto['erro'] = str(exc)
    else:
        if mni.falha_de_autenticacao(resultado):
            # Senha recusada: descarta o hash e pede de novo.
            senhas.esquecer(request, sistema)
            contexto['pedir_senha'] = True
            contexto['erro_senha'] = ' '.join(resultado.mensagens)
        contexto['resultado'] = resultado
    contexto['consultado_em'] = timezone.localtime()
    return render(request, 'judicial/sistemas/_avisos.html', contexto)


@require_POST
def informar_senha(request, pk):
    """Recebe usuário e senha do sistema judicial. Só o hash SHA-256 da senha vai para o cache."""
    sistema = get_object_or_404(SistemaJudicial, pk=pk, ativo=True)
    destino = _destino_seguro(request, request.POST.get('next')) or reverse('sistemas:ambientes', args=[sistema.pk])
    login = request.POST.get('login', '').strip()
    senha = request.POST.get('senha', '')
    if not login or not senha:
        messages.error(request, f'Informe o usuário e a senha do {sistema.nome}.')
        return redirect(destino)
    if len(login) > Credencial._meta.get_field('login').max_length:
        messages.error(request, 'Usuário muito longo.')
        return redirect(destino)

    Credencial.objects.update_or_create(usuario=request.user, sistema=sistema, defaults={'login': login})
    senhas.guardar(request, sistema, senha)
    del senha
    return redirect(destino)


@require_POST
def esquecer_senha(request, pk):
    sistema = get_object_or_404(SistemaJudicial, pk=pk)
    senhas.esquecer(request, sistema)
    messages.success(request, f'Acesso ao {sistema.nome} encerrado. A senha será pedida novamente.')
    return redirect(_destino_seguro(request, request.POST.get('next')) or 'sistemas:lista')


@require_POST
def definir_padrao(request, sistema_pk, pk):
    ambiente = _ambiente_ativo(sistema_pk, pk)
    definir_ambiente_padrao(request.user, ambiente)
    messages.success(
        request, f'{ambiente.sistema.nome} · {ambiente.descricao} agora é o seu ambiente padrão. '
        'Ele será aberto automaticamente quando você entrar no EloJudicial.'
    )
    return redirect('sistemas:servicos', sistema_pk=ambiente.sistema_id, pk=ambiente.pk)


@require_POST
def remover_padrao(request):
    definir_ambiente_padrao(request.user, None)
    messages.success(request, 'Ambiente padrão removido. Ao entrar, você irá para a página inicial.')
    return redirect(_destino_seguro(request, request.POST.get('next')) or 'sistemas:lista')
