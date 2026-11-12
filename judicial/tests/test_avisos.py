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
from judicial.models import AvisoPendente, ConsultaAvisos, Credencial, SistemaJudicial

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


class RegistroAvisosTests(TestCase):
    """O tribunal entrega cada aviso pendente uma única vez: tudo o que chega fica guardado."""

    SENHA = 'S3nh@'
    NUMERO_1 = '0001234-14.2024.8.27.2729'
    ROTULO_NOVO = 'novo\n</span>'

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
        self.client.post(
            reverse('sistemas:informar_senha', args=[self.eproc.pk]), {'login': '12345678900', 'senha': self.SENHA}
        )

    def consultar(self, resposta):
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(resposta)):
            return self.client.get(self.url)

    def test_grava_a_resposta_bruta_e_cada_aviso_como_pendente(self):
        self.consultar(RESPOSTA_OK)
        consulta = ConsultaAvisos.objects.get()
        self.assertEqual(bytes(consulta.resposta), RESPOSTA_OK)
        self.assertEqual((consulta.usuario, consulta.ambiente, consulta.login), (self.user, self.amb, '12345678900'))
        self.assertEqual((consulta.sucesso, consulta.total_avisos), (True, 2))

        avisos = AvisoPendente.objects.order_by('id_aviso')
        self.assertEqual([a.id_aviso for a in avisos], ['000000000000001', '000000000000002'])
        self.assertTrue(all(a.pendente and a.usuario == self.user and a.consulta == consulta for a in avisos))
        primeiro = avisos[0]
        self.assertEqual((primeiro.tipo_codigo, primeiro.numero_processo, primeiro.prazo), ('INT', '00012341420248272729', 15))
        self.assertEqual(primeiro.orgao_julgador, '1ª Vara Cível de Palmas')
        # O XML original guarda também o que não é exibido (ex.: classe processual).
        self.assertIn('classeProcessual', primeiro.xml)
        self.assertIn('000000000000001', primeiro.xml)

    def test_avisos_continuam_na_tela_depois_que_o_tribunal_deixa_de_devolve_los(self):
        resp = self.consultar(RESPOSTA_OK)
        self.assertContains(resp, '2 avisos pendentes')
        self.assertContains(resp, self.ROTULO_NOVO, count=2)

        resp = self.consultar(RESPOSTA_SEM_AVISOS)  # o tribunal já os entregou
        self.assertContains(resp, '2 avisos pendentes')
        self.assertContains(resp, self.NUMERO_1)
        self.assertNotContains(resp, self.ROTULO_NOVO)
        self.assertNotContains(resp, 'Tudo em dia!')

    def test_aviso_devolvido_de_novo_nao_e_duplicado(self):
        self.consultar(RESPOSTA_OK)
        self.consultar(RESPOSTA_OK)
        self.assertEqual(ConsultaAvisos.objects.count(), 2)
        self.assertEqual(AvisoPendente.objects.count(), 2)
        self.assertEqual(set(AvisoPendente.objects.values_list('consulta', flat=True)), {ConsultaAvisos.objects.last().pk})

    def test_resposta_que_nao_pode_ser_interpretada_fica_gravada(self):
        resp = self.consultar(b'<resposta-quebrada')
        self.assertContains(resp, 'resposta inválida')
        consulta = ConsultaAvisos.objects.get()
        self.assertEqual(bytes(consulta.resposta), b'<resposta-quebrada')
        self.assertIsNone(consulta.sucesso)

    def test_falha_de_conexao_nao_grava_nada(self):
        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('x')):
            self.client.get(self.url)
        self.assertFalse(ConsultaAvisos.objects.exists())

    def test_erro_do_tribunal_mantem_os_avisos_guardados_na_tela(self):
        self.consultar(RESPOSTA_OK)
        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('x')):
            resp = self.client.get(self.url)
        self.assertContains(resp, 'Não foi possível conectar ao tribunal')
        self.assertContains(resp, self.NUMERO_1)

    def test_aviso_que_deixou_de_ser_pendente_sai_da_lista(self):
        self.consultar(RESPOSTA_OK)
        AvisoPendente.objects.filter(id_aviso='000000000000001').update(pendente=False)
        resp = self.consultar(RESPOSTA_SEM_AVISOS)
        self.assertContains(resp, '1 aviso pendente')
        self.assertNotContains(resp, self.NUMERO_1)

    def test_sem_senha_a_pagina_mostra_os_avisos_guardados(self):
        self.consultar(RESPOSTA_OK)
        self.client.post(reverse('sistemas:esquecer_senha', args=[self.eproc.pk]))
        resp = self.client.get(reverse('sistemas:servicos', args=[self.eproc.pk, self.amb.pk]))
        self.assertContains(resp, 'Informe sua senha do EPROC TJTO')
        self.assertContains(resp, '2 avisos pendentes')
        self.assertContains(resp, self.NUMERO_1)
        self.assertNotContains(resp, 'hx-trigger="load"')  # sem senha não consulta o tribunal

    def test_ambiente_com_avisos_nao_pode_ser_apagado(self):
        from django.db.models import ProtectedError

        self.consultar(RESPOSTA_OK)
        with self.assertRaises(ProtectedError):
            self.amb.delete()

    def test_excluir_o_usuario_remove_os_avisos_dele(self):
        self.consultar(RESPOSTA_OK)
        self.user.delete()
        self.assertFalse(AvisoPendente.objects.exists())
        self.assertFalse(ConsultaAvisos.objects.exists())


class ConsultaAutomaticaUmaVezPorSessaoTests(TestCase):
    """O consultarAvisosPendentes é disparado sozinho só na 1ª abertura do ambiente após o login."""

    SENHA = 'S3nh@'

    def setUp(self):
        cache.clear()
        p = mock.patch.object(wsdl, '_baixar', return_value=WSDL)
        p.start()
        self.addCleanup(p.stop)
        self.user = get_user_model().objects.create_user('ana', email='ana@exemplo.com', password='x')
        self.client.force_login(self.user)
        self.eproc = SistemaJudicial.objects.get(nome='EPROC TJTO')
        self.amb1, self.amb2 = self.eproc.ambientes.order_by('ordem')[:2]
        self.informar_senha()

    def informar_senha(self):
        self.client.post(
            reverse('sistemas:informar_senha', args=[self.eproc.pk]), {'login': '12345678900', 'senha': self.SENHA}
        )

    def pagina(self, ambiente):
        return self.client.get(reverse('sistemas:servicos', args=[self.eproc.pk, ambiente.pk]))

    def consultar(self, ambiente, resposta=RESPOSTA_OK):
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(resposta)):
            return self.client.get(reverse('sistemas:avisos', args=[self.eproc.pk, ambiente.pk]))

    def test_primeira_abertura_consulta_automaticamente(self):
        self.assertContains(self.pagina(self.amb1), 'hx-trigger="load"')

    def test_depois_da_consulta_mostra_os_guardados_sem_consultar_de_novo(self):
        self.pagina(self.amb1)
        self.consultar(self.amb1)
        resp = self.pagina(self.amb1)
        self.assertNotContains(resp, 'hx-trigger="load"')
        self.assertContains(resp, '2 avisos pendentes')
        self.assertContains(resp, 'última consulta em')
        self.assertContains(resp, 'Consultar novos avisos')

    def test_botao_continua_consultando_o_tribunal(self):
        self.consultar(self.amb1)
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(RESPOSTA_SEM_AVISOS)) as urlopen:
            self.client.get(reverse('sistemas:avisos', args=[self.eproc.pk, self.amb1.pk]))
        urlopen.assert_called_once()

    def test_cada_ambiente_tem_a_sua_primeira_consulta(self):
        self.consultar(self.amb1)
        self.assertContains(self.pagina(self.amb2), 'hx-trigger="load"')

    def test_novo_login_volta_a_consultar_automaticamente(self):
        self.consultar(self.amb1)
        self.client.logout()
        self.client.force_login(self.user)
        self.informar_senha()
        self.assertContains(self.pagina(self.amb1), 'hx-trigger="load"')

    def test_sem_senha_nao_conta_como_consultado(self):
        self.client.post(reverse('sistemas:esquecer_senha', args=[self.eproc.pk]))
        with mock.patch('urllib.request.urlopen') as urlopen:
            self.client.get(reverse('sistemas:avisos', args=[self.eproc.pk, self.amb1.pk]))
        urlopen.assert_not_called()
        self.informar_senha()
        self.assertContains(self.pagina(self.amb1), 'hx-trigger="load"')
