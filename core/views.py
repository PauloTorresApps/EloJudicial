from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_not_required
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views.decorators.http import require_http_methods

from judicial.preferencias import ambiente_padrao

from .emails import enviar_confirmacao_email
from .forms import CadastroForm, ReenviarConfirmacaoForm
from .tokens import ativacao_token


def inicio(request):
    # Acesso restrito a usuários logados via LoginRequiredMiddleware.
    return render(request, 'core/inicio.html', {'padrao': ambiente_padrao(request.user)})


@login_not_required
@require_http_methods(['GET', 'POST'])
def cadastro(request):
    if request.user.is_authenticated:
        return redirect('inicio')

    if request.method == 'POST':
        form = CadastroForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = False  # só ativa após confirmar o e-mail
            try:
                with transaction.atomic():
                    user.save()
            except IntegrityError:
                # Outro cadastro com o mesmo e-mail/usuário foi gravado entre a
                # validação do formulário e o save (o banco garante a unicidade).
                form.add_error(None, 'Este e-mail ou nome de usuário acabou de ser cadastrado. Verifique os dados.')
            else:
                enviar_confirmacao_email(request, user)
                return redirect('cadastro_enviado')
    else:
        form = CadastroForm()

    return render(request, 'contas/cadastro.html', {'form': form})


@login_not_required
def cadastro_enviado(request):
    return render(request, 'contas/cadastro_enviado.html')


@login_not_required
def ativar_conta(request, uidb64, token):
    User = get_user_model()
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is None or not ativacao_token.check_token(user, token):
        return render(request, 'contas/ativacao_invalida.html', status=400)

    user.is_active = True
    user.save(update_fields=['is_active'])
    login(request, user)
    messages.success(request, 'E-mail confirmado! Sua conta está ativa.')
    return redirect('inicio')


@login_not_required
@require_http_methods(['GET', 'POST'])
def reenviar_confirmacao(request):
    if request.method == 'POST':
        form = ReenviarConfirmacaoForm(request.POST)
        if form.is_valid():
            # Só reenvia para contas nunca ativadas. A resposta é sempre a mesma,
            # para não revelar quais e-mails estão cadastrados.
            pendentes = get_user_model().objects.filter(
                email__iexact=form.cleaned_data['email'], is_active=False, last_login__isnull=True
            )
            for user in pendentes:
                enviar_confirmacao_email(request, user)
            return redirect('cadastro_enviado')
    else:
        form = ReenviarConfirmacaoForm()

    return render(request, 'contas/reenviar_confirmacao.html', {'form': form})
