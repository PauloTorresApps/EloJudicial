from django.contrib.auth.tokens import PasswordResetTokenGenerator


class AtivacaoContaTokenGenerator(PasswordResetTokenGenerator):
    """Token de confirmação de e-mail.

    Usa um salt próprio (não serve como token de redefinição de senha) e inclui
    `is_active` no hash, então deixa de valer assim que a conta é ativada.
    Validade: settings.PASSWORD_RESET_TIMEOUT (padrão de 3 dias).
    """

    key_salt = 'core.tokens.AtivacaoContaTokenGenerator'

    def _make_hash_value(self, user, timestamp):
        return f'{super()._make_hash_value(user, timestamp)}{user.is_active}'


ativacao_token = AtivacaoContaTokenGenerator()
