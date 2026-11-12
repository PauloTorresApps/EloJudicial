from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from .forms import CredencialForm
from .models import Credencial, SistemaJudicial


def _credencial_do_usuario(request, pk):
    # Cada usuário só enxerga as próprias credenciais (404 para as dos outros).
    return get_object_or_404(Credencial.objects.select_related('sistema'), pk=pk, usuario=request.user)


def lista(request):
    credenciais = request.user.credenciais_judiciais.select_related('sistema')
    return render(request, 'judicial/lista.html', {
        'credenciais': credenciais,
        'ha_sistemas_disponiveis': SistemaJudicial.objects.filter(ativo=True)
        .exclude(credenciais__usuario=request.user).exists(),
    })


def _voltar(request):
    """Volta para a página de origem (?next=), se for deste site."""
    destino = request.POST.get('next') or request.GET.get('next')
    if destino and url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}):
        return redirect(destino)
    return None


def _salvar(request, form, mensagem):
    try:
        with transaction.atomic():
            form.save()
    except IntegrityError:
        form.add_error('sistema', 'Você já tem uma credencial cadastrada para este sistema.')
        return False
    messages.success(request, mensagem)
    return True


@require_http_methods(['GET', 'POST'])
def criar(request):
    inicial = {'sistema': request.GET['sistema']} if request.GET.get('sistema', '').isdigit() else None
    form = CredencialForm(request.POST or None, initial=inicial, usuario=request.user)
    if request.method == 'POST' and form.is_valid() and _salvar(request, form, 'Credencial cadastrada.'):
        return _voltar(request) or redirect('judicial:lista')
    return render(request, 'judicial/form.html', {'form': form, 'titulo': 'Nova credencial'})


@require_http_methods(['GET', 'POST'])
def editar(request, pk):
    credencial = _credencial_do_usuario(request, pk)
    form = CredencialForm(request.POST or None, instance=credencial, usuario=request.user)
    if request.method == 'POST' and form.is_valid() and _salvar(request, form, 'Credencial atualizada.'):
        return _voltar(request) or redirect('judicial:lista')
    return render(request, 'judicial/form.html', {'form': form, 'titulo': 'Editar credencial', 'credencial': credencial})


@require_http_methods(['GET', 'POST'])
def excluir(request, pk):
    credencial = _credencial_do_usuario(request, pk)
    if request.method == 'POST':
        credencial.delete()
        messages.success(request, 'Credencial excluída.')
        return redirect('judicial:lista')
    return render(request, 'judicial/excluir.html', {'credencial': credencial})
