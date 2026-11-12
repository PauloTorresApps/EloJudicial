from django.apps import AppConfig


class JudicialConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'judicial'
    verbose_name = 'Sistemas judiciais'

    def ready(self):
        from . import senhas  # noqa: F401  (registra o sinal que limpa as senhas ao sair)
