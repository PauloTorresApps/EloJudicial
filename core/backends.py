from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOuUsuarioBackend(ModelBackend):
    """Permite entrar com o nome de usuário ou com o e-mail.

    - Texto com "@" é tratado como e-mail (sem diferenciar maiúsculas/minúsculas).
    - Texto sem "@" é tratado como nome de usuário.
    Se mais de uma conta tiver o mesmo e-mail, o login por e-mail é recusado
    (a pessoa ainda pode entrar pelo nome de usuário).
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if username is None or password is None:
            return None

        username = username.strip()
        try:
            if '@' in username:
                user = User._default_manager.get(email__iexact=username)
            else:
                user = User._default_manager.get_by_natural_key(username)
        except (User.DoesNotExist, User.MultipleObjectsReturned):
            # Executa o hasher mesmo sem usuário para não revelar, pelo tempo de
            # resposta, se a conta existe (mesmo cuidado do ModelBackend).
            User().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
