from django.db import migrations
from django.db.models import Count
from django.db.models.functions import Lower

INDICE = 'core_auth_user_email_unico'


def verificar_duplicados(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    duplicados = list(
        User.objects.exclude(email='')
        .annotate(email_normalizado=Lower('email'))
        .values('email_normalizado')
        .annotate(total=Count('id'))
        .filter(total__gt=1)
        .values_list('email_normalizado', flat=True)
    )
    if duplicados:
        raise RuntimeError(
            'Não é possível tornar o e-mail único: há e-mails repetidos '
            f'(sem diferenciar maiúsculas/minúsculas): {", ".join(duplicados)}. '
            'Corrija esses usuários e rode a migração de novo.'
        )


class Migration(migrations.Migration):
    """Índice único, sem diferenciar maiúsculas/minúsculas, no e-mail do usuário.

    E-mails vazios ficam fora do índice (o admin permite criar usuários sem e-mail).
    Funciona em PostgreSQL e SQLite.
    """

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(verificar_duplicados, migrations.RunPython.noop),
        migrations.RunSQL(
            sql=f"CREATE UNIQUE INDEX {INDICE} ON auth_user (LOWER(email)) WHERE email <> ''",
            reverse_sql=f'DROP INDEX {INDICE}',
        ),
    ]
