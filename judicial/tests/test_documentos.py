import base64
from unittest import mock

from django.urls import reverse

from judicial import mni

from .test_avisos import RESPOSTA_FALHA_AUTH, _Resposta
from .test_processos import NUMERO, RESPOSTA_PROCESSO, Base

PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF'
HTML = '<html><body><h1>Decisão</h1><script>alert(1)</script></body></html>'.encode()

CABECALHO = (
    b'<?xml version="1.0" encoding="UTF-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"'
    b' xmlns:ns1="http://www.cnj.jus.br/mni/v300/intercomunicacao" xmlns:ns2="http://www.cnj.jus.br/mni/v300/tipos-servico-intercomunicacao"'
    b' xmlns:ns3="http://www.cnj.jus.br/mni/v300/" xmlns:xop="http://www.w3.org/2004/08/xop/include"><SOAP-ENV:Body>'
    b'<ns3:respostaConsultarDocumentosProcesso><ns2:recibo><ns1:sucesso>true</ns1:sucesso><ns1:mensagens>'
    b'<ns1:descritivo>ok</ns1:descritivo><ns1:codigo>0</ns1:codigo><ns1:codigoUnico>x</ns1:codigoUnico></ns1:mensagens></ns2:recibo>'
)
RODAPE = b'</ns3:respostaConsultarDocumentosProcesso></SOAP-ENV:Body></SOAP-ENV:Envelope>'


def resposta_base64(dados, mimetype, id_doc='d1'):
    return (CABECALHO + f'<ns2:documentos><ns1:idDocumento>{id_doc}</ns1:idDocumento><ns1:mimetype>{mimetype}</ns1:mimetype>'
            f'<ns1:conteudo>{base64.b64encode(dados).decode()}</ns1:conteudo></ns2:documentos>'.encode() + RODAPE)


def resposta_mtom(dados, mimetype, id_doc='d1'):
    xml = (CABECALHO + f'<ns2:documentos><ns1:idDocumento>{id_doc}</ns1:idDocumento><ns1:mimetype>{mimetype}</ns1:mimetype>'
           '<ns1:conteudo><xop:Include href="cid:anexo1%40jus.br"/></ns1:conteudo></ns2:documentos>'.encode() + RODAPE)
    corpo = (b'--MIME_fronteira\r\nContent-Type: application/xop+xml; charset=UTF-8; type="text/xml"\r\n'
             b'Content-Transfer-Encoding: 8bit\r\nContent-ID: <raiz@jus.br>\r\n\r\n' + xml +
             b'\r\n--MIME_fronteira\r\nContent-Type: application/octet-stream\r\nContent-Transfer-Encoding: binary\r\n'
             b'Content-ID: <anexo1@jus.br>\r\n\r\n' + dados + b'\r\n--MIME_fronteira--\r\n')
    tipo = 'multipart/related; type="application/xop+xml"; boundary="MIME_fronteira"; start="<raiz@jus.br>"; start-info="text/xml"'
    return corpo, tipo


class _RespostaComTipo(_Resposta):
    def __init__(self, conteudo, tipo):
        super().__init__(conteudo)
        self.headers = {'Content-Type': tipo}


class ClienteDocumentosTests(Base):
    def test_envelope(self):
        corpo = mni._envelope_consultar_documentos('123', 'hash', NUMERO, ['d1']).decode()
        posicoes = [corpo.index(f':{n}') for n in ('consultante', 'numeroProcesso', 'idDocumento')]
        self.assertEqual(posicoes, sorted(posicoes))
        self.assertIn('idDocumento>d1<', corpo)

    def test_conteudo_em_base64(self):
        r = mni.interpretar_documentos(resposta_base64(PDF, 'application/pdf'))
        self.assertTrue(r.sucesso)
        self.assertEqual((r.documentos[0].id, r.documentos[0].mimetype, r.documentos[0].dados), ('d1', 'application/pdf', PDF))

    def test_conteudo_em_mtom(self):
        corpo, tipo = resposta_mtom(PDF, 'application/pdf')
        r = mni.interpretar_documentos(corpo, tipo)
        self.assertEqual(r.documentos[0].dados, PDF)

    def test_mtom_sem_anexo_correspondente(self):
        corpo, tipo = resposta_mtom(PDF, 'application/pdf')
        with self.assertRaises(mni.ErroMNI):
            mni.interpretar_documentos(corpo.replace(b'<anexo1@jus.br>', b'<outro@jus.br>'), tipo)

    def test_base64_corrompido(self):
        with self.assertRaisesMessage(mni.ErroMNI, 'corrompido'):
            mni.interpretar_documentos(resposta_base64(PDF, 'application/pdf').replace(b'<ns1:conteudo>', b'<ns1:conteudo>@@'))

    def test_consulta_usa_limite_maior_e_le_content_type(self):
        corpo, tipo = resposta_mtom(PDF, 'application/pdf')
        with mock.patch('urllib.request.urlopen', return_value=_RespostaComTipo(corpo, tipo)) as urlopen:
            r = mni.consultar_documento(self.amb1, '123', 'hash', NUMERO, 'd1')
        self.assertEqual(r.documentos[0].dados, PDF)
        self.assertEqual(urlopen.call_args.args[0].get_header('Soapaction'),
                         '"http://www.cnj.jus.br/mni/v300/consultarDocumentosProcesso"')


class ViewDocumentoTests(Base):
    def url(self, id_doc='d1', **params):
        u = reverse('sistemas:documento', args=[self.eproc.pk, self.amb1.pk, NUMERO, id_doc])
        return u + ('?' + '&'.join(f'{k}={v}' for k, v in params.items()) if params else '')

    def abrir(self, resposta, **params):
        with mock.patch('urllib.request.urlopen', return_value=resposta) as urlopen:
            return self.client.get(self.url(**params)), urlopen

    def test_pdf_exibido_no_navegador(self):
        self.informar_senha()
        resp, urlopen = self.abrir(_Resposta(resposta_base64(PDF, 'application/pdf')), nome='DECDESPA1')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, PDF)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertEqual(resp['Content-Disposition'], 'inline; filename="DECDESPA1.pdf"')
        self.assertEqual(resp['Cache-Control'], 'private, no-store')
        self.assertEqual(resp['X-Content-Type-Options'], 'nosniff')
        corpo = urlopen.call_args.args[0].data.decode()
        self.assertIn('idDocumento>d1<', corpo)

    def test_html_isolado_sem_scripts(self):
        self.informar_senha()
        resp, _ = self.abrir(_Resposta(resposta_base64(HTML, 'text/html')), nome='CERT1')
        self.assertEqual(resp['Content-Type'], 'text/html; charset=utf-8')
        self.assertIn('sandbox', resp['Content-Security-Policy'])
        self.assertEqual(resp['Content-Disposition'], 'inline; filename="CERT1.html"')

    def test_baixar(self):
        self.informar_senha()
        resp, _ = self.abrir(_Resposta(resposta_base64(PDF, 'application/pdf')), nome='LAUDO1', baixar='1')
        self.assertEqual(resp['Content-Disposition'], 'attachment; filename="LAUDO1.pdf"')

    def test_tipo_desconhecido_sempre_baixa(self):
        self.informar_senha()
        resp, _ = self.abrir(_Resposta(resposta_base64(b'MZ\x90', 'application/x-msdownload')))
        self.assertTrue(resp['Content-Disposition'].startswith('attachment;'))

    def test_nome_do_arquivo_sanitizado(self):
        self.assertEqual(__import__('judicial.views_processos', fromlist=['x'])._nome_arquivo('../a"b\r\nc', 'application/pdf'), 'a_b_c.pdf')

    def test_sem_senha_volta_ao_processo(self):
        with mock.patch('urllib.request.urlopen') as urlopen:
            resp = self.client.get(self.url(), follow=True)
        urlopen.assert_not_called()
        self.assertRedirects(resp, self.url_processo(self.amb1))
        self.assertContains(resp, 'Informe sua senha do EPROC TJTO para abrir o documento')

    def test_senha_recusada(self):
        self.informar_senha()
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(RESPOSTA_FALHA_AUTH)):
            resp = self.client.get(self.url(), follow=True)
        self.assertContains(resp, 'O tribunal recusou a senha')
        with mock.patch('urllib.request.urlopen') as urlopen:
            self.client.get(self.url())
        urlopen.assert_not_called()

    def test_documento_sem_conteudo_ou_erro(self):
        self.informar_senha()
        vazio = CABECALHO.replace(b'true', b'false').replace(b'>ok<', b'>Documento criptografado<') + RODAPE
        resp, _ = self.abrir(_Resposta(vazio))
        self.assertRedirects(resp, self.url_processo(self.amb1), fetch_redirect_response=False)
        resp = self.client.get(self.url_processo(self.amb1))
        self.assertContains(resp, 'Documento criptografado')

    def test_exige_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url()).status_code, 302)

    def test_lista_de_eventos_tem_links_para_os_documentos(self):
        self.informar_senha()
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(RESPOSTA_PROCESSO)):
            resp = self.client.get(self.url_dados(self.amb1))
        self.assertContains(resp, f'href="{self.url("d1")}?nome=INIC1"')
        self.assertContains(resp, f'href="{self.url("d1a")}?nome=PROC1"')
        self.assertContains(resp, 'target="_blank" rel="noopener"')
