from django.db import migrations

SISTEMAS = [
    ('EPROC', 'Processo judicial eletrônico (eproc)'),
    ('PROJUD', 'Processo Judicial Digital'),
    ('PJe', 'Processo Judicial Eletrônico (CNJ)'),
]


def criar(apps, schema_editor):
    SistemaJudicial = apps.get_model('judicial', 'SistemaJudicial')
    for nome, descricao in SISTEMAS:
        if not SistemaJudicial.objects.filter(nome__iexact=nome).exists():
            SistemaJudicial.objects.create(nome=nome, descricao=descricao)


def remover(apps, schema_editor):
    SistemaJudicial = apps.get_model('judicial', 'SistemaJudicial')
    SistemaJudicial.objects.filter(nome__in=[n for n, _ in SISTEMAS], credenciais__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [('judicial', '0001_initial')]
    operations = [migrations.RunPython(criar, remover)]
