from django.apps import AppConfig


class IngestaoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ingestao'
    verbose_name = 'Ingestão de documentos'

    def ready(self):
        from . import acesso, models  # noqa: F401  (sinais: acesso ao sair; arquivos ao apagar)
