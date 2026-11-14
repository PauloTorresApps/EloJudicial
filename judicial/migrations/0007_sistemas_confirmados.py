"""Sistemas judiciais com endpoint MNI confirmado em página ou documento do próprio tribunal.

Fonte: planilha "Endpoints MNI — Tribunais do Brasil" (pesquisa de 13/11/2026), linhas com
Situação "Confirmado". Nenhuma URL foi testada ao vivo: teste o WSDL antes de usar.
A versão do MNI de cada endpoint fica na descrição do ambiente quando não é a 3.0.
"""

from django.db import migrations

# (nome, descrição, ativo, [(ordem, ambiente, URL do WSDL)])
SISTEMAS = [
    ('STF', 'Sistema próprio do Supremo Tribunal Federal (MNI 2.2.2)', True, [
        (1, 'Grau único', 'https://ws.stf.jus.br/servico-intercomunicacao-2.2.2/intercomunicacao?wsdl'),
    ]),
    ('ESAJ TJAM', 'e-SAJ do Tribunal de Justiça do Estado do Amazonas (MNI 2.2.2)', True, [
        (1, '1º e 2º Grau', 'https://consultasaj.tjam.jus.br/mniws/servico-intercomunicacao-2.2.2/intercomunicacao?wsdl'),
    ]),
    # Webservice de consulta processual do Projudi, não é o contrato MNI: fica inativo.
    ('PROJUDI TJAM', 'Projudi do Tribunal de Justiça do Estado do Amazonas (webservice próprio, não MNI)', False, [
        (1, '1º e 2º Grau', 'https://projudi.tjam.jus.br/projudi/webservices/consultaProcessualWebService?wsdl'),
    ]),
    ('PJE TJBA', 'PJe do Tribunal de Justiça do Estado da Bahia (MNI 2.2.2)', True, [
        (1, '1º Grau', 'https://pje1g-mni.tjba.jus.br/pje/intercomunicacao?wsdl'),
        (2, '2º Grau', 'https://pje2g-mni.tjba.jus.br/pje/intercomunicacao?wsdl'),
    ]),
    ('PJE TJES', 'PJe do Tribunal de Justiça do Estado do Espírito Santo (MNI 2.2.2)', True, [
        (1, '1º Grau', 'https://pje.tjes.jus.br/pje/intercomunicacao?wsdl'),
    ]),
    ('PJE TJMA', 'PJe do Tribunal de Justiça do Estado do Maranhão (MNI 2.2.3)', True, [
        (1, '1º Grau', 'https://pje.tjma.jus.br/pje/intercomunicacao/v223?wsdl'),
        (2, '2º Grau', 'https://pje2.tjma.jus.br/pje2g/intercomunicacao/v223?wsdl'),
    ]),
    ('PROJUDI TJPR', 'Projudi do Tribunal de Justiça do Estado do Paraná (MNI 2.2.2)', True, [
        (1, '1º Grau', 'https://projudi.tjpr.jus.br/projudi_consulta/webservices/projudiIntercomunicacaoWebService222?wsdl'),
        (2, '2º Grau', 'https://projudi.tjpr.jus.br/projudi_consulta/webservices/projudiIntercomunicacaoWebService2G222?wsdl'),
    ]),
    ('PJE TJPE', 'PJe do Tribunal de Justiça do Estado de Pernambuco (MNI 2.2.2)', True, [
        (1, '1º Grau', 'https://pjemni.app.tjpe.jus.br/1g/servico-intercomunicacao-2.2.2?wsdl'),
        (2, '2º Grau', 'https://pjemni.app.tjpe.jus.br/2g/servico-intercomunicacao-2.2.2?wsdl'),
    ]),
    ('PJE TJPI', 'PJe do Tribunal de Justiça do Estado do Piauí (MNI 2.2.2)', True, [
        (1, '1º Grau', 'https://pje.tjpi.jus.br/1g/intercomunicacao?wsdl'),
        (2, '2º Grau', 'https://pje.tjpi.jus.br/2g/intercomunicacao?wsdl'),
    ]),
    # WSDL em arquivo único. Equivalente, com imports externos: https://www12.tjrj.jus.br/MNI/Servico.svc?wsdl
    ('MNI TJRJ', 'Serviço MNI próprio do Tribunal de Justiça do Estado do Rio de Janeiro (MNI 2.2.2)', True, [
        (1, '1º e 2º Grau', 'https://www12.tjrj.jus.br/MNI/Servico.svc?singleWsdl'),
    ]),
    ('PJE TRF1', 'PJe do Tribunal Regional Federal da 1ª Região (MNI 2.2.3)', True, [
        (1, '1º Grau', 'https://pje1g.trf1.jus.br/pje/intercomunicacao/v223?wsdl'),
        (2, '2º Grau', 'https://pje2g.trf1.jus.br/pje/intercomunicacao/v223?wsdl'),
    ]),
    ('PJE-JT TRT1', 'PJe da Justiça do Trabalho — TRT da 1ª Região', True, [
        (1, '1º e 2º Grau', 'https://pje.trt1.jus.br/pje-integracao-api/mni300/intercomunicacao?wsdl'),
    ]),
    ('PJE-JT TRT6', 'PJe da Justiça do Trabalho — TRT da 6ª Região', True, [
        (1, '1º e 2º Grau', 'https://pje.trt6.jus.br/pje-integracao-api/mni300/intercomunicacao?wsdl'),
    ]),
    ('PJE-JT TRT15', 'PJe da Justiça do Trabalho — TRT da 15ª Região', True, [
        (1, '1º e 2º Grau', 'https://pje.trt15.jus.br/pje-integracao-api/mni300/intercomunicacao?wsdl'),
    ]),
    ('PJE TRE-DF', 'PJe do Tribunal Regional Eleitoral do Distrito Federal (MNI 2.2.2)', True, [
        (1, '1º Grau (zonas eleitorais)', 'https://pje1g-df.tse.jus.br/pje/intercomunicacao?wsdl'),
        (2, '2º Grau', 'https://pje.tre-df.jus.br/pje/intercomunicacao?wsdl'),
    ]),
    ('EPROC TJM-RS', 'eproc do Tribunal de Justiça Militar do Estado do Rio Grande do Sul (MNI 2.2)', True, [
        (1, '1º Grau', 'https://eproc1g.tjmrs.jus.br/eproc/wsdl.php?srv=intercomunicacao2.2'),
        (2, '2º Grau', 'https://eproc2g.tjmrs.jus.br/eproc/wsdl.php?srv=intercomunicacao2.2'),
    ]),
]


def criar(apps, schema_editor):
    SistemaJudicial = apps.get_model('judicial', 'SistemaJudicial')
    Ambiente = apps.get_model('judicial', 'Ambiente')

    for nome, descricao, ativo, ambientes in SISTEMAS:
        sistema = SistemaJudicial.objects.filter(nome__iexact=nome).first()
        if sistema is None:
            sistema = SistemaJudicial.objects.create(nome=nome, descricao=descricao, ativo=ativo)
        for ordem, ambiente, url in ambientes:
            existe = Ambiente.objects.filter(sistema=sistema, descricao__iexact=ambiente).exists() or \
                Ambiente.objects.filter(sistema=sistema, url_servico=url).exists()
            if not existe:
                Ambiente.objects.create(sistema=sistema, descricao=ambiente, url_servico=url, ordem=ordem)


def remover(apps, schema_editor):
    SistemaJudicial = apps.get_model('judicial', 'SistemaJudicial')
    SistemaJudicial.objects.filter(nome__in=[s[0] for s in SISTEMAS], credenciais__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [('judicial', '0006_avisos_pendentes')]
    operations = [migrations.RunPython(criar, remover)]
