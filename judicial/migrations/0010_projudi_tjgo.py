"""Projudi do TJGO, MNI 2.2.2.

O WSDL foi baixado e interpretado em 16/11/2026: namespace
http://www.cnj.jus.br/servico-intercomunicacao-2.2.2/, com o serviço em
https://projudi.tjgo.jus.br/IntercomunicacaoService. Um único endereço atende 1º e 2º grau.
Além das operações do MNI, publica a operação própria consultarRelatorioDeIntimacoesTJGO.
"""

from django.db import migrations

NOME = 'PROJUDI TJGO'
DESCRICAO = 'Projudi do Tribunal de Justiça do Estado de Goiás (MNI 2.2.2)'
AMBIENTES = [
    (1, '1º e 2º Grau', 'https://projudi.tjgo.jus.br/IntercomunicacaoService?WSDL'),
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
    dependencies = [('judicial', '0009_eproc_tjto_mni22')]
    operations = [migrations.RunPython(criar, remover)]
