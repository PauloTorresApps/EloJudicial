"""Campo homologacao: ambientes de homologação ficam ocultos quando ELOJUDICIAL_PRODUCAO=True."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('judicial', '0012_tipos_documento_marcados')]
    operations = [
        migrations.AddField(
            model_name='ambiente',
            name='homologacao',
            field=models.BooleanField(
                default=False, verbose_name='homologação',
                help_text='Serviço de testes do tribunal. Fica oculto quando ELOJUDICIAL_PRODUCAO=True.',
            ),
        ),
    ]
