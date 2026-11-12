from django.db import migrations

NOME = 'EPROC TJTO'
DESCRICAO = 'eproc do Tribunal de Justiça do Estado do Tocantins'
AMBIENTES = [
    (1, '1º Grau', 'https://eproc1g-ws.tjto.jus.br/eprocV2_prod_1grau/wsdl.php?srv=intercomunicacao3.0'),
    (2, '2º Grau', 'https://eproc2g-ws.tjto.jus.br/eprocV2_prod_2grau/wsdl.php?srv=intercomunicacao3.0'),
]


def criar(apps, schema_editor):
    SistemaJudicial = apps.get_model('judicial', 'SistemaJudicial')
    Ambiente = apps.get_model('judicial', 'Ambiente')

    sistema = SistemaJudicial.objects.filter(nome__iexact=NOME).first()
    if sistema is None:
        # O "EPROC" genérico criado na carga inicial passa a ser o EPROC do TJTO.
        sistema = SistemaJudicial.objects.filter(nome__iexact='EPROC').first()
        if sistema is not None:
            sistema.nome = NOME
            sistema.descricao = DESCRICAO
            sistema.save()
        else:
            sistema = SistemaJudicial.objects.create(nome=NOME, descricao=DESCRICAO)

    for ordem, descricao, url in AMBIENTES:
        if not Ambiente.objects.filter(sistema=sistema, descricao__iexact=descricao).exists():
            Ambiente.objects.create(sistema=sistema, descricao=descricao, url_servico=url, ordem=ordem)


class Migration(migrations.Migration):
    dependencies = [('judicial', '0003_ambientes')]
    operations = [migrations.RunPython(criar, migrations.RunPython.noop)]
