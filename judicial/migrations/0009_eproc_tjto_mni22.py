"""Ambientes MNI 2.2 do eproc do TJTO, ao lado dos de MNI 3.0.

O eproc do TJTO publica as duas versões do MNI. Os WSDLs da 2.2 foram validados em
14/11/2026 (namespace http://www.cnj.jus.br/servico-intercomunicacao-2.2.2/), e a
autenticação e a consulta de avisos foram testadas com uma credencial real.
Mesma credencial dos ambientes 3.0, pois o sistema judicial é o mesmo.
"""

from django.db import migrations

NOME = 'EPROC TJTO'
AMBIENTES = [
    (3, '1º Grau (MNI 2.2)', 'https://eproc1g-ws.tjto.jus.br/eprocV2_prod_1grau/wsdl.php?srv=intercomunicacao2.2'),
    (4, '2º Grau (MNI 2.2)', 'https://eproc2g-ws.tjto.jus.br/eprocV2_prod_2grau/wsdl.php?srv=intercomunicacao2.2'),
]


def criar(apps, schema_editor):
    SistemaJudicial = apps.get_model('judicial', 'SistemaJudicial')
    Ambiente = apps.get_model('judicial', 'Ambiente')

    sistema = SistemaJudicial.objects.filter(nome__iexact=NOME).first()
    if sistema is None:
        return
    for ordem, descricao, url in AMBIENTES:
        existe = Ambiente.objects.filter(sistema=sistema, descricao__iexact=descricao).exists() or \
            Ambiente.objects.filter(sistema=sistema, url_servico=url).exists()
        if not existe:
            Ambiente.objects.create(sistema=sistema, descricao=descricao, url_servico=url, ordem=ordem)


def remover(apps, schema_editor):
    Ambiente = apps.get_model('judicial', 'Ambiente')
    Ambiente.objects.filter(sistema__nome=NOME, url_servico__in=[url for _, _, url in AMBIENTES]).delete()


class Migration(migrations.Migration):
    dependencies = [('judicial', '0008_eproc_tjmg')]
    operations = [migrations.RunPython(criar, remover)]
