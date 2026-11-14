"""eproc do TJMG, MNI 3.0.

Na planilha de endpoints aparecia como "Padrão (não confirmado)". Os dois WSDLs foram
baixados e interpretados em 14/11/2026: MNI 3.0 (namespace http://www.cnj.jus.br/mni/v300/),
com o serviço em eproc1g-ws / eproc2g-ws, como no TJTO.
"""

from django.db import migrations

NOME = 'EPROC TJMG'
DESCRICAO = 'eproc do Tribunal de Justiça do Estado de Minas Gerais'
AMBIENTES = [
    (1, '1º Grau', 'https://eproc1g.tjmg.jus.br/eproc/wsdl.php?srv=intercomunicacao3.0'),
    (2, '2º Grau', 'https://eproc2g.tjmg.jus.br/eproc/wsdl.php?srv=intercomunicacao3.0'),
]


def criar(apps, schema_editor):
    SistemaJudicial = apps.get_model('judicial', 'SistemaJudicial')
    Ambiente = apps.get_model('judicial', 'Ambiente')

    sistema = SistemaJudicial.objects.filter(nome__iexact=NOME).first()
    if sistema is None:
        sistema = SistemaJudicial.objects.create(nome=NOME, descricao=DESCRICAO)
    for ordem, descricao, url in AMBIENTES:
        if not Ambiente.objects.filter(sistema=sistema, descricao__iexact=descricao).exists():
            Ambiente.objects.create(sistema=sistema, descricao=descricao, url_servico=url, ordem=ordem)


def remover(apps, schema_editor):
    SistemaJudicial = apps.get_model('judicial', 'SistemaJudicial')
    SistemaJudicial.objects.filter(nome=NOME, credenciais__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [('judicial', '0007_sistemas_confirmados')]
    operations = [migrations.RunPython(criar, remover)]
