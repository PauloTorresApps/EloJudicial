from django.conf import settings
from django.contrib.auth import views as auth_views
from django.shortcuts import resolve_url

from judicial.preferencias import url_ambiente_padrao


class LoginView(auth_views.LoginView):
    """Login que leva o usuário direto ao seu ambiente de trabalho padrão.

    Se o usuário estava indo para uma página específica (parâmetro "next"),
    ela tem prioridade. "next" apontando para a página inicial não conta como
    destino específico: é o que acontece quando alguém simplesmente abre o site.
    """

    def get_success_url(self):
        destino = self.get_redirect_url()
        if destino and destino != resolve_url(settings.LOGIN_REDIRECT_URL):
            return destino
        return url_ambiente_padrao(self.request.user) or self.get_default_redirect_url()
