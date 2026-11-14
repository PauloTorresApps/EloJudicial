"""MNI 2.2.2: escolha da versão pelo WSDL, envelopes, interpretação e telas."""

import base64
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from defusedxml import ElementTree

from judicial import mni, wsdl
from judicial.avisos import consultar_e_registrar
from judicial.models import AvisoPendente, SistemaJudicial

from .test_avisos import WSDL as WSDL_300
from .test_avisos import _Resposta
from .test_documentos import _RespostaComTipo

DADOS = Path(__file__).parent / 'dados'
WSDL = (DADOS / 'servico_222.wsdl').read_bytes()
RESPOSTA_AVISOS = (DADOS / 'avisos_222.xml').read_bytes()
RESPOSTA_PROCESSO = (DADOS / 'processo_222.xml').read_bytes()
NUMERO = '00012341420248272729'
BRASILIA = ZoneInfo('America/Sao_Paulo')
S = 'http://www.cnj.jus.br/servico-intercomunicacao-2.2.2/'
T = 'http://www.cnj.jus.br/tipos-servico-intercomunicacao-2.2.2'

RESPOSTA_FALHA_AUTH = f'''<?xml version="1.0"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>
<ns4:consultarAvisosPendentesResposta xmlns:ns3="{T}" xmlns:ns4="{S}"><ns3:sucesso>false</ns3:sucesso>
<ns3:mensagem>Usuário ou senha inválidos.</ns3:mensagem></ns4:consultarAvisosPendentesResposta></soap:Body></soap:Envelope>'''.encode()


def resposta_documento(dados, mimetype='application/pdf', vinculado=False):
    """consultarProcesso com o conteúdo de um documento (opcionalmente dentro de um documento vinculado)."""
    conteudo = f'<ns2:conteudo>{base64.b64encode(dados).decode()}</ns2:conteudo>'
    doc = (f'<ns2:documento idDocumento="d1" mimetype="application/pdf"><ns2:documentoVinculado idDocumento="d1a" '
           f'mimetype="{mimetype}">{conteudo}</ns2:documentoVinculado></ns2:documento>' if vinculado else
           f'<ns2:documento idDocumento="d1" mimetype="{mimetype}">{conteudo}</ns2:documento>')
    return (f'<?xml version="1.0"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
            f'<ns4:consultarProcessoResposta xmlns:ns2="http://www.cnj.jus.br/intercomunicacao-2.2.2" xmlns:ns3="{T}" '
            f'xmlns:ns4="{S}"><ns3:sucesso>true</ns3:sucesso><ns3:mensagem>ok</ns3:mensagem><ns3:processo>{doc}'
            f'</ns3:processo></ns4:consultarProcessoResposta></soap:Body></soap:Envelope>').encode()


def filhos_da_requisicao(corpo):
    """[(namespace, nome, texto)] dos filhos do elemento de requisição, na ordem enviada."""
    req = ElementTree.fromstring(corpo)[0][0]
    return req.tag, [(f.tag.split('}')[0][1:], f.tag.split('}')[1], f.text) for f in req]


class Base(TestCase):
    SENHA = 'S3nh@'

    def setUp(self):
        cache.clear()
        p = mock.patch.object(wsdl, '_baixar', return_value=WSDL)
        p.start()
        self.addCleanup(p.stop)
        self.sistema = SistemaJudicial.objects.get(nome='PJE TJBA')
        self.ambiente = self.sistema.ambientes.order_by('ordem').first()


class EscolhaDaVersaoTests(Base):
    def test_wsdl_2_2_2_usa_o_cliente_2_2_2(self):
        cliente = mni.cliente(self.ambiente)
        self.assertIsInstance(cliente, mni.ClienteMNI222)
        self.assertEqual(cliente.endereco, 'https://pje.exemplo.jus.br/pje/intercomunicacao')

    def test_wsdl_3_0_usa_o_cliente_3_0(self):
        with mock.patch.object(wsdl, '_baixar', return_value=WSDL_300):
            self.assertIsInstance(mni.cliente(self.ambiente), mni.ClienteMNI300)

    def test_versao_desconhecida(self):
        outro = WSDL.replace(b'servico-intercomunicacao-2.2.2/', b'servico-intercomunicacao-9.9/')
        with mock.patch.object(wsdl, '_baixar', return_value=outro), \
                self.assertRaisesMessage(mni.ErroMNI, 'ainda não é suportada'):
            mni.cliente(self.ambiente)

    def test_uma_classe_por_versao(self):
        from judicial.mni.versoes import CLIENTES
        self.assertEqual(CLIENTES, {
            'http://www.cnj.jus.br/mni/v300/': mni.ClienteMNI300,
            'http://www.cnj.jus.br/servico-intercomunicacao-2.2.2/': mni.ClienteMNI222,
        })
        self.assertFalse(issubclass(mni.ClienteMNI222, mni.ClienteMNI300))
        self.assertFalse(issubclass(mni.ClienteMNI300, mni.ClienteMNI222))


class AvisosTests(Base):
    def consultar(self, resposta=RESPOSTA_AVISOS):
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(resposta)) as urlopen:
            return mni.consultar_avisos_pendentes(self.ambiente, '123', 'S3nh@<&>'), urlopen.call_args.args[0]

    def test_requisicao_segue_o_xsd(self):
        _, req = self.consultar()
        self.assertEqual(req.full_url, 'https://pje.exemplo.jus.br/pje/intercomunicacao')
        self.assertEqual(req.get_header('Soapaction'), f'"{S}consultarAvisosPendentes"')
        raiz, campos = filhos_da_requisicao(req.data)
        self.assertEqual(raiz, f'{{{S}}}consultarAvisosPendentes')
        self.assertEqual(campos, [(T, 'idConsultante', '123'), (T, 'senhaConsultante', 'S3nh@<&>')])
        self.assertIn(b'S3nh@&lt;&amp;&gt;', req.data)  # caracteres especiais escapados

    def test_interpreta_avisos(self):
        r, _ = self.consultar()
        self.assertTrue(r.sucesso)
        self.assertEqual(r.mensagens, ['Avisos consultados com sucesso'])
        recente, antigo = r.avisos  # do mais recente para o mais antigo
        self.assertEqual((recente.id, recente.tipo, recente.destinatario), ('9002', 'Citação', 'Ministério Público'))
        self.assertEqual(antigo.tipo, 'Intimação')
        self.assertEqual(antigo.numero_processo_formatado, '0001234-14.2024.8.27.2729')
        self.assertEqual(antigo.destinatario, 'MINISTÉRIO PÚBLICO DO ESTADO DA BAHIA')
        self.assertEqual(antigo.orgao_julgador, '1ª Vara Cível de Salvador')
        self.assertEqual(antigo.data_disponibilizacao, datetime(2026, 11, 5, 10, 30, tzinfo=BRASILIA))
        self.assertEqual(antigo.prazo_descricao, '—')  # o aviso do MNI 2.2 não traz prazo
        self.assertIn('idAviso="9001"', antigo.xml)

    def test_falha_de_autenticacao(self):
        r, _ = self.consultar(RESPOSTA_FALHA_AUTH)
        self.assertFalse(r.sucesso)
        self.assertTrue(mni.falha_de_autenticacao(r))

    def test_avisos_sao_gravados(self):
        usuario = get_user_model().objects.create_user('ana', email='ana@exemplo.com', password='x')
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(RESPOSTA_AVISOS)):
            _, novos = consultar_e_registrar(usuario, self.ambiente, '123', 'h')
        self.assertEqual(len(novos), 2)
        aviso = AvisoPendente.objects.get(id_aviso='9001')
        self.assertEqual(aviso.data_disponibilizacao, datetime(2026, 11, 5, 10, 30, tzinfo=BRASILIA))


class ProcessoTests(Base):
    def test_requisicao_segue_o_xsd(self):
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(RESPOSTA_PROCESSO)) as urlopen:
            mni.consultar_processo(self.ambiente, '123', 'h', NUMERO)
        req = urlopen.call_args.args[0]
        self.assertEqual(req.get_header('Soapaction'), f'"{S}consultarProcesso"')
        raiz, campos = filhos_da_requisicao(req.data)
        self.assertEqual(raiz, f'{{{S}}}consultarProcesso')
        self.assertEqual(campos, [
            (T, 'idConsultante', '123'), (T, 'senhaConsultante', 'h'), (T, 'numeroProcesso', NUMERO),
            (T, 'movimentos', 'true'), (T, 'incluirCabecalho', 'true'), (T, 'incluirDocumentos', 'true'),
        ])

    def test_capa_e_partes(self):
        r = mni.ClienteMNI222.interpretar_processo(RESPOSTA_PROCESSO)
        p = r.processo
        self.assertTrue(r.sucesso)
        self.assertEqual(p.numero_formatado, '0001234-14.2024.8.27.2729')
        self.assertEqual((p.classe, p.competencia, p.codigo_localidade), ('7', '14', '2729'))
        self.assertEqual(p.orgao_julgador, '1ª Vara Cível de Salvador')
        self.assertEqual(p.data_ajuizamento, datetime(2024, 3, 15, 14, 20, tzinfo=BRASILIA))
        self.assertEqual(p.valor_causa, 15000.5)
        self.assertEqual(p.nivel_sigilo, 0)
        self.assertEqual(p.outros_numeros, ['5000123-45.2024.8.05.0001'])
        self.assertEqual(p.prioridades_descricao, ['Idoso'])
        self.assertEqual(p.assuntos, ['Indenização por Dano Moral', 'Assunto 7780'])
        self.assertEqual(p.informacoes_adicionais, [('Segredo justica', 'NAO')])
        ativo, passivo = p.polos_principais
        self.assertEqual(ativo.partes[0].tipo_pessoa_descricao, 'Pessoa Jurídica')
        self.assertEqual(ativo.partes[0].documento_formatado, '04.142.491/0001-66')
        reu = passivo.partes[0]
        self.assertEqual((reu.nome, reu.documento_formatado), ('FULANO DE TAL', '123.456.789-09'))
        self.assertEqual(reu.representantes[0].inscricao_formatada, 'OAB/BA 1234')

    def test_eventos_e_documentos(self):
        p = mni.ClienteMNI222.interpretar_processo(RESPOSTA_PROCESSO).processo
        self.assertEqual([(m.numero, m.descricao) for m in p.movimentos], [
            (3, 'Juntada de petição'), (2, 'Decisão proferida'), (1, 'Distribuído por sorteio'),
        ])
        juntada, decisao, distribuicao = p.movimentos
        self.assertEqual(juntada.complementos, [])
        # Documento ligado pelo atributo "movimento" e pelo idDocumentoVinculado do evento.
        self.assertEqual([d.nome for d in distribuicao.documentos], ['Petição inicial'])
        self.assertEqual([d.nome for d in distribuicao.documentos[0].vinculados], ['Procuração'])
        self.assertEqual([d.id for d in decisao.documentos], ['d2'])
        self.assertEqual(decisao.documentos[0].mimetype, 'text/html')

    def test_nao_encontrado(self):
        resposta = RESPOSTA_FALHA_AUTH.replace(b'Usu\xc3\xa1rio ou senha inv\xc3\xa1lidos.', b'Processo n\xc3\xa3o encontrado')
        r = mni.ClienteMNI222.interpretar_processo(resposta)
        self.assertIsNone(r.processo)
        self.assertEqual(r.mensagens, ['Processo não encontrado'])
        self.assertFalse(mni.falha_de_autenticacao(r))

    def test_resposta_grande_demais_repete_sem_documentos(self):
        respostas = [mni.RespostaGrandeDemais('grande'), RESPOSTA_PROCESSO]
        with mock.patch.object(mni.ClienteMNI, '_enviar', side_effect=respostas) as enviar:
            r = mni.consultar_processo(self.ambiente, '1', 'h', NUMERO)
        self.assertTrue(r.documentos_omitidos)
        self.assertIn(b'incluirDocumentos>false<', enviar.call_args_list[1].args[1])

    def test_sem_tabela_de_competencias(self):
        p = mni.ClienteMNI222.interpretar_processo(RESPOSTA_PROCESSO).processo
        with mock.patch('urllib.request.urlopen') as urlopen:
            mni.descrever_competencia(self.ambiente, p)
        urlopen.assert_not_called()
        self.assertEqual(p.competencia_descricao, '')


class DocumentoTests(Base):
    PDF = b'%PDF-1.4 teste'

    def test_requisicao_pede_o_documento_ao_consultar_processo(self):
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(resposta_documento(self.PDF))) as urlopen:
            mni.consultar_documento(self.ambiente, '123', 'h', NUMERO, 'd1')
        req = urlopen.call_args.args[0]
        self.assertEqual(req.get_header('Soapaction'), f'"{S}consultarProcesso"')
        _, campos = filhos_da_requisicao(req.data)
        self.assertEqual(campos, [
            (T, 'idConsultante', '123'), (T, 'senhaConsultante', 'h'), (T, 'numeroProcesso', NUMERO),
            (T, 'movimentos', 'false'), (T, 'incluirCabecalho', 'false'), (T, 'incluirDocumentos', 'true'),
            (T, 'documento', 'd1'),
        ])

    def test_conteudo_em_base64(self):
        r = mni.ClienteMNI222.interpretar_documentos(resposta_documento(self.PDF))
        self.assertTrue(r.sucesso)
        self.assertEqual([(d.id, d.mimetype, d.dados) for d in r.documentos], [('d1', 'application/pdf', self.PDF)])

    def test_documento_vinculado(self):
        r = mni.ClienteMNI222.interpretar_documentos(resposta_documento(b'<p>oi</p>', 'text/html', vinculado=True))
        self.assertEqual([(d.id, d.mimetype) for d in r.documentos], [('d1a', 'text/html')])

    def test_conteudo_em_mtom(self):
        xml = resposta_documento(b'x').replace(
            base64.b64encode(b'x'), b'<xop:Include xmlns:xop="http://www.w3.org/2004/08/xop/include" href="cid:a1%40jus.br"/>')
        corpo = (b'--F\r\nContent-Type: application/xop+xml; type="text/xml"\r\nContent-ID: <raiz@jus.br>\r\n\r\n' + xml +
                 b'\r\n--F\r\nContent-Type: application/octet-stream\r\nContent-Transfer-Encoding: binary\r\n'
                 b'Content-ID: <a1@jus.br>\r\n\r\n' + self.PDF + b'\r\n--F--\r\n')
        r = mni.ClienteMNI222.interpretar_documentos(corpo, 'multipart/related; type="application/xop+xml"; boundary="F"')
        self.assertEqual(r.documentos[0].dados, self.PDF)


class TelasTests(Base):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user('ana', email='ana@exemplo.com', password='x')
        self.client.force_login(self.user)
        self.client.post(reverse('sistemas:informar_senha', args=[self.sistema.pk]), {'login': '123', 'senha': self.SENHA})

    def test_dados_do_processo(self):
        url = reverse('sistemas:processo_dados', args=[self.sistema.pk, self.ambiente.pk, NUMERO])
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(RESPOSTA_PROCESSO)) as urlopen:
            resposta = self.client.get(url)
        self.assertEqual(urlopen.call_count, 1)  # sem consulta de competências no MNI 2.2
        self.assertContains(resposta, 'FULANO DE TAL')
        self.assertContains(resposta, 'Decisão proferida')
        self.assertContains(resposta, '1ª Vara Cível de Salvador')
        # A senha segue o mesmo tratamento do sistema: só o hash é enviado.
        self.assertIn(sha256(self.SENHA.encode()).hexdigest().encode(), urlopen.call_args.args[0].data)

    def test_avisos_pendentes(self):
        url = reverse('sistemas:avisos', args=[self.sistema.pk, self.ambiente.pk])
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(RESPOSTA_AVISOS)):
            resposta = self.client.get(url)
        self.assertContains(resposta, 'Citação')
        self.assertContains(resposta, '0001234-14.2024.8.27.2729')

    def test_abrir_documento(self):
        url = reverse('sistemas:documento', args=[self.sistema.pk, self.ambiente.pk, NUMERO, 'd1'])
        resposta_tribunal = _RespostaComTipo(resposta_documento(DocumentoTests.PDF), 'text/xml')
        with mock.patch('urllib.request.urlopen', return_value=resposta_tribunal):
            resposta = self.client.get(url)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta['Content-Type'], 'application/pdf')
        self.assertEqual(resposta.content, DocumentoTests.PDF)
