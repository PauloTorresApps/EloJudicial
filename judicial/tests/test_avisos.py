import urllib.error
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from judicial import mni, wsdl
from judicial.models import Credencial, SistemaJudicial

DADOS = Path(__file__).parent / 'dados'
RESPOSTA_OK = (DADOS / 'avisos_resposta.xml').read_bytes()
WSDL = (DADOS / 'servico_exemplo.wsdl').read_bytes()
RESPOSTA_FALHA_AUTH = '''<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="http://www.cnj.jus.br/mni/v300/intercomunicacao" xmlns:ns2="http://www.cnj.jus.br/mni/v300/tipos-servico-intercomunicacao" xmlns:ns3="http://www.cnj.jus.br/mni/v300/"><SOAP-ENV:Body><ns3:respostaConsultarAvisosPendentes><ns2:recibo><ns1:sucesso>false</ns1:sucesso><ns1:mensagens><ns1:descritivo>-&gt;Falha na autenticação do usuário 123 [Senha ou usuário Inválidos  ]</ns1:descritivo><ns1:codigo>1</ns1:codigo><ns1:codigoUnico>x</ns1:codigoUnico></ns1:mensagens></ns2:recibo></ns3:respostaConsultarAvisosPendentes></SOAP-ENV:Body></SOAP-ENV:Envelope>'''.encode()
RESPOSTA_SEM_AVISOS = RESPOSTA_OK.split(b'<ns2:avisos>')[0] + b'</ns3:respostaConsultarAvisosPendentes></SOAP-ENV:Body></SOAP-ENV:Envelope>'
FAULT = b'''<?xml version="1.0"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"><SOAP-ENV:Body><SOAP-ENV:Fault><faultcode>SOAP-ENV:Server</faultcode><faultstring>Erro interno no servidor</faultstring></SOAP-ENV:Fault></SOAP-ENV:Body></SOAP-ENV:Envelope>'''


class _Resposta:
    def __init__(self, conteudo):
        self._c = conteudo

    def read(self, n=-1):
        return self._c

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ClienteMNITests(TestCase):
    def setUp(self):
        cache.clear()
        self.ambiente = SistemaJudicial.objects.get(nome='EPROC TJTO').ambientes.order_by('ordem').first()
        p = mock.patch.object(wsdl, '_baixar', return_value=WSDL)
        p.start()
        self.addCleanup(p.stop)

    def _consultar(self, **kw):
        with mock.patch('urllib.request.urlopen', **kw) as urlopen:
            resultado = mni.consultar_avisos_pendentes(self.ambiente, '123', 'S3nh@<&>')
        return resultado, urlopen

    def test_requisicao_enviada(self):
        _, urlopen = self._consultar(return_value=_Resposta(RESPOSTA_OK))
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, 'https://exemplo.jus.br/ws/controlador_ws.php?srv=intercomunicacao3.0')
        self.assertEqual(req.get_header('Soapaction'), '"http://www.cnj.jus.br/mni/v300/consultarAvisosPendentes"')
        corpo = req.data.decode()
        self.assertIn('requisicaoConsultarAvisosPendentes', corpo)
        self.assertIn('>123<', corpo)
        self.assertIn('S3nh@&lt;&amp;&gt;', corpo)  # caracteres especiais escapados

    def test_interpreta_avisos(self):
        r, _ = self._consultar(return_value=_Resposta(RESPOSTA_OK))
        self.assertTrue(r.sucesso)
        self.assertEqual(len(r.avisos), 2)
        recente, antigo = r.avisos  # ordenados do mais recente para o mais antigo
        self.assertEqual(recente.tipo, 'Citação')
        self.assertEqual(recente.prazo_descricao, 'Sem prazo')
        self.assertEqual(antigo.tipo, 'Intimação')
        self.assertEqual(antigo.numero_processo_formatado, '0001234-14.2024.8.27.2729')
        self.assertEqual(antigo.destinatario, 'MINISTÉRIO PÚBLICO DO ESTADO DO TOCANTINS')
        self.assertEqual(antigo.orgao_julgador, '1ª Vara Cível de Palmas')
        self.assertEqual(antigo.prazo_descricao, '15 dias')
        self.assertEqual(antigo.data_disponibilizacao.isoformat(), '2026-11-05T10:30:00-03:00')

    def test_falha_de_autenticacao(self):
        r, _ = self._consultar(return_value=_Resposta(RESPOSTA_FALHA_AUTH))
        self.assertFalse(r.sucesso)
        self.assertEqual(r.mensagens, ['Falha na autenticação do usuário 123 [Senha ou usuário Inválidos  ]'])

    def test_soap_fault_com_http_500(self):
        erro = urllib.error.HTTPError('u', 500, 'erro', {}, BytesIO(FAULT))
        with self.assertRaisesMessage(mni.ErroMNI, 'Erro interno no servidor'):
            self._consultar(side_effect=erro)

    def test_timeout_e_falha_de_conexao(self):
        for exc, msg in ((TimeoutError(), 'demorou demais'), (urllib.error.URLError('x'), 'Não foi possível conectar')):
            with self.assertRaisesMessage(mni.ErroMNI, msg):
                self._consultar(side_effect=exc)

    def test_resposta_invalida(self):
        with self.assertRaisesMessage(mni.ErroMNI, 'resposta inválida'):
            self._consultar(return_value=_Resposta(b'<html>erro</html>'))

    def test_wsdl_indisponivel_gera_mensagem_sem_url(self):
        with mock.patch.object(wsdl, '_baixar', side_effect=wsdl.ErroWSDL('falha em https://eproc1g-ws.tjto.jus.br')):
            cache.clear()
            with self.assertRaises(mni.ErroMNI) as ctx:
                mni.consultar_avisos_pendentes(self.ambiente, '1', '2')
        self.assertNotIn('http', str(ctx.exception))


class TelaAvisosTests(TestCase):
    SENHA = 'S3nh@'

    def setUp(self):
        cache.clear()
        p = mock.patch.object(wsdl, '_baixar', return_value=WSDL)
        p.start()
        self.addCleanup(p.stop)
        self.user = get_user_model().objects.create_user('ana', email='ana@exemplo.com', password='x')
        self.client.force_login(self.user)
        self.eproc = SistemaJudicial.objects.get(nome='EPROC TJTO')
        self.amb = self.eproc.ambientes.order_by('ordem').first()
        self.url = reverse('sistemas:avisos', args=[self.eproc.pk, self.amb.pk])

    def _informar_senha(self, senha=SENHA):
        self.client.post(reverse('sistemas:informar_senha', args=[self.eproc.pk]), {'login': '12345678900', 'senha': senha})

    def _get(self, resposta):
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(resposta)) as urlopen:
            return self.client.get(self.url), urlopen

    def test_sem_senha_pede_senha_e_nao_chama_o_tribunal(self):
        with mock.patch('urllib.request.urlopen') as urlopen:
            resp = self.client.get(self.url)
        urlopen.assert_not_called()
        self.assertContains(resp, 'Informe sua senha do EPROC TJTO')
        self.assertContains(resp, reverse('sistemas:informar_senha', args=[self.eproc.pk]))

    def test_envia_hash_sha256_da_senha_no_envelope(self):
        self._informar_senha()
        resp, urlopen = self._get(RESPOSTA_OK)
        corpo = urlopen.call_args.args[0].data.decode()
        self.assertIn(f'<ns3:senha>{sha256(self.SENHA.encode("utf-8")).hexdigest()}</ns3:senha>', corpo)
        self.assertNotIn(self.SENHA, corpo)
        self.assertIn('<ns3:usuario>12345678900</ns3:usuario>', corpo)

    def test_lista_avisos(self):
        self._informar_senha()
        resp, _ = self._get(RESPOSTA_OK)
        self.assertContains(resp, '2 avisos pendentes')
        self.assertContains(resp, '0001234-14.2024.8.27.2729')
        self.assertContains(resp, 'Citação')
        self.assertContains(resp, '15 dias')
        self.assertNotContains(resp, 'https://')

    def test_sem_avisos(self):
        self._informar_senha()
        resp, _ = self._get(RESPOSTA_SEM_AVISOS)
        self.assertContains(resp, 'Nenhum aviso pendente')
        self.assertContains(resp, 'Tudo em dia!')

    def test_senha_recusada_descarta_hash_e_pede_de_novo(self):
        self._informar_senha()
        resp, _ = self._get(RESPOSTA_FALHA_AUTH)
        self.assertContains(resp, 'Senha ou usuário Inválidos')
        self.assertContains(resp, 'Informe sua senha do EPROC TJTO')
        with mock.patch('urllib.request.urlopen') as urlopen:
            self.client.get(self.url)
        urlopen.assert_not_called()  # o hash foi descartado

    def test_erro_de_conexao_mantem_a_senha(self):
        self._informar_senha()
        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('x')):
            resp = self.client.get(self.url)
        self.assertContains(resp, 'Não foi possível conectar ao tribunal')
        resp, urlopen = self._get(RESPOSTA_OK)
        urlopen.assert_called_once()

    def test_ambiente_de_outro_sistema(self):
        pje = SistemaJudicial.objects.get(nome='PJe')
        self.assertEqual(self.client.get(reverse('sistemas:avisos', args=[pje.pk, self.amb.pk])).status_code, 404)

    def test_exige_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_pagina_do_ambiente_dispara_consulta_automatica_quando_ha_senha(self):
        pagina = reverse('sistemas:servicos', args=[self.eproc.pk, self.amb.pk])
        resp = self.client.get(pagina)
        self.assertNotContains(resp, 'hx-trigger="load"')
        self.assertContains(resp, 'x-data="{ aberto: true }"')
        self._informar_senha()
        resp = self.client.get(pagina)
        self.assertContains(resp, f'hx-get="{self.url}"')
        self.assertContains(resp, 'hx-trigger="load"')
        self.assertContains(resp, 'Consultando avisos pendentes')
        self.assertContains(resp, 'Conectado como')
