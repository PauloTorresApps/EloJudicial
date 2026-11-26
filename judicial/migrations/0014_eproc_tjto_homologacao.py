"""Ambientes de homologação (MNI 3.0) do eproc do TJTO: 1º e 2º Grau.

Ambientes marcados como homologação ficam ocultos quando ELOJUDICIAL_PRODUCAO=True.
Os WSDLs foram validados em 25/11/2026 (namespace http://www.cnj.jus.br/mni/v300/,
execução em https://eproc{1,2}g.hmlg.tjto.jus.br/eproc{1,2}g-homolog/ws/controlador_ws.php).
"""

from django.db import migrations
from django.db.models import Q

NOME = 'EPROC TJTO'
AMBIENTES = [
    (10, '1º Grau (Homologação)', 'https://eproc1g.hmlg.tjto.jus.br/eproc1g-homolog/wsdl.php?srv=intercomunicacao3.0'),
    (11, '2º Grau (Homologação)', 'https://eproc2g.hmlg.tjto.jus.br/eproc2g-homolog/wsdl.php?srv=intercomunicacao3.0'),
]


def criar(apps, schema_editor):
    SistemaJudicial = apps.get_model('judicial', 'SistemaJudicial')
    Ambiente = apps.get_model('judicial', 'Ambiente')

    sistema = SistemaJudicial.objects.filter(nome__iexact=NOME).first()
    if sistema is None:
        return
    for ordem, descricao, url in AMBIENTES:
        if not Ambiente.objects.filter(Q(descricao__iexact=descricao) | Q(url_servico=url), sistema=sistema).exists():
            Ambiente.objects.create(sistema=sistema, descricao=descricao, url_servico=url, ordem=ordem, homologacao=True)


def remover(apps, schema_editor):
    Ambiente = apps.get_model('judicial', 'Ambiente')
    Ambiente.objects.filter(sistema__nome=NOME, url_servico__in=[url for _, _, url in AMBIENTES]).delete()


class Migration(migrations.Migration):
    dependencies = [('judicial', '0013_ambiente_homologacao')]
    operations = [migrations.RunPython(criar, remover)]
