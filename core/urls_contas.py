from django.contrib.auth import views as auth_views
from django.urls import path

from . import forms, views
from .views_login import LoginView

# Rotas de autenticação da área do usuário. Os nomes são os mesmos de
# django.contrib.auth.urls, mas com templates próprios (pasta "contas/"), para
# não sobrescrever os templates "registration/*" usados pelo admin.
urlpatterns = [
    path('login/', LoginView.as_view(
        template_name='contas/login.html',
        authentication_form=forms.LoginForm,
        redirect_authenticated_user=True,
    ), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),

    path('cadastro/', views.cadastro, name='cadastro'),
    path('cadastro/enviado/', views.cadastro_enviado, name='cadastro_enviado'),
    path('cadastro/reenviar/', views.reenviar_confirmacao, name='reenviar_confirmacao'),
    path('ativar/<uidb64>/<token>/', views.ativar_conta, name='ativar_conta'),

    path('senha/alterar/', auth_views.PasswordChangeView.as_view(
        template_name='contas/senha_alterar.html',
        form_class=forms.AlterarSenhaForm,
    ), name='password_change'),
    path('senha/alterar/concluido/', auth_views.PasswordChangeDoneView.as_view(
        template_name='contas/senha_alterar_concluido.html',
    ), name='password_change_done'),

    path('senha/recuperar/', auth_views.PasswordResetView.as_view(
        template_name='contas/senha_recuperar.html',
        email_template_name='contas/emails/recuperar_senha.txt',
        subject_template_name='contas/emails/recuperar_senha_assunto.txt',
        form_class=forms.RecuperarSenhaForm,
    ), name='password_reset'),
    path('senha/recuperar/enviado/', auth_views.PasswordResetDoneView.as_view(
        template_name='contas/senha_recuperar_enviado.html',
    ), name='password_reset_done'),
    path('senha/redefinir/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='contas/senha_redefinir.html',
        form_class=forms.NovaSenhaForm,
    ), name='password_reset_confirm'),
    path('senha/redefinir/concluido/', auth_views.PasswordResetCompleteView.as_view(
        template_name='contas/senha_redefinir_concluido.html',
    ), name='password_reset_complete'),
]
