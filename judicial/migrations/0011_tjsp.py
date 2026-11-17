"""e-SAJ e eproc do TJSP, ambos MNI 2.2.2.

Os WSDLs foram baixados e interpretados em 16/11/2026 (namespace
http://www.cnj.jus.br/servico-intercomunicacao-2.2.2/).

e-SAJ: como no TJAM, um único endereço (mniws) atende 1º e 2º grau; o caminho mniws2 responde 404.
O WSDL anuncia o serviço em http://esaj.tjsp.jus.br/mniws/servico-intercomunicacao-2.2.2/intercomunicacao.

eproc: o WSDL é servido por wsdl.php (controlador_ws.php é o endpoint SOAP e responde vazio a GET).
Os serviços ficam nos hosts eproc1gws e eproc2gws.
"""

from django.db import migrations

SISTEMAS = [
    ('ESAJ TJSP', 'e-SAJ do Tribunal de Justiça do Estado de São Paulo (MNI 2.2.2)', [
        (1, '1º e 2º Grau', 'https://esaj.tjsp.jus.br/mniws/servico-intercomunicacao-2.2.2/intercomunicacao?wsdl'),
    ]),
    ('EPROC TJSP', 'eproc do Tribunal de Justiça do Estado de São Paulo (MNI 2.2.2)', [
        (1, '1º Grau', 'https://eproc1g.tjsp.jus.br/eproc/wsdl.php?srv=intercomunicacao2.2'),
        (2, '2º Grau', 'https://eproc2g.tjsp.jus.br/eproc/wsdl.php?srv=intercomunicacao2.2'),
    ]),
]


def criar(apps, schema_editor):
    SistemaJudicial = apps.get_model('judicial', 'SistemaJudicial')
    Ambiente = apps.get_model('judicial', 'Ambiente')

    for nome, descricao, ambientes in SISTEMAS:
        sistema = SistemaJudicial.objects.filter(nome__iexact=nome).first()
        if sistema is None:
            sistema = SistemaJudicial.objects.create(nome=nome, descricao=descricao)
        for ordem, desc_ambiente, url in ambientes:
            if not Ambiente.objects.filter(sistema=sistema, descricao__iexact=desc_ambiente).exists():
                Ambiente.objects.create(sistema=sistema, descricao=desc_ambiente, url_servico=url, ordem=ordem)


def remover(apps, schema_editor):
    SistemaJudicial = apps.get_model('judicial', 'SistemaJudicial')
    SistemaJudicial.objects.filter(nome__in=[nome for nome, _, _ in SISTEMAS], credenciais__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [('judicial', '0010_projudi_tjgo')]
    operations = [migrations.RunPython(criar, remover)]
