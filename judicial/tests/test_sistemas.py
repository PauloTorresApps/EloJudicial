from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from judicial import wsdl
from judicial.models import Ambiente, Credencial, SistemaJudicial

WSDL_EXEMPLO = (Path(__file__).parent / 'dados' / 'servico_exemplo.wsdl').read_bytes()
URL_1G = 'https://eproc1g-ws.tjto.jus.br/eprocV2_prod_1grau/wsdl.php?srv=intercomunicacao3.0'
URL_2G = 'https://eproc2g-ws.tjto.jus.br/eprocV2_prod_2grau/wsdl.php?srv=intercomunicacao3.0'


class DadosIniciaisTests(TestCase):
    def test_eproc_tjto_com_ambientes_1g_e_2g(self):
        sistema = SistemaJudicial.objects.get(nome='EPROC TJTO')
        self.assertEqual(
            list(sistema.ambientes.values_list('descricao', 'url_servico')),
            [('1º Grau', URL_1G), ('2º Grau', URL_2G)],
        )
        self.assertFalse(SistemaJudicial.objects.filter(nome='EPROC').exists())


class AmbienteModeloTests(TestCase):
    def setUp(self):
        self.sistema = SistemaJudicial.objects.get(nome='EPROC TJTO')

    def test_descricao_unica_por_sistema_sem_diferenciar_maiusculas(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Ambiente.objects.create(sistema=self.sistema, descricao='1º GRAU', url_servico='https://x.jus.br/a')

    def test_url_unica_por_sistema(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Ambiente.objects.create(sistema=self.sistema, descricao='Outro', url_servico=URL_1G)

    def test_mesma_descricao_em_sistemas_diferentes(self):
        pje = SistemaJudicial.objects.get(nome='PJe')
        Ambiente.objects.create(sistema=pje, descricao='1º Grau', url_servico='https://pje.jus.br/ws')

    def test_excluir_sistema_sem_credenciais_remove_ambientes(self):
        pk = self.sistema.pk  # delete() zera o pk da instância
        self.assertTrue(Ambiente.objects.filter(sistema_id=pk).exists())
        self.sistema.delete()
        self.assertFalse(Ambiente.objects.filter(sistema_id=pk).exists())


class InterpretarWSDLTests(TestCase):
    def test_operacoes_endereco_e_nome(self):
        d = wsdl.interpretar(WSDL_EXEMPLO)
        self.assertEqual(d.nome, 'mni')
        self.assertEqual(d.endereco, 'https://exemplo.jus.br/ws/controlador_ws.php?srv=intercomunicacao3.0')
        self.assertEqual(len(d.operacoes), 5)

    def test_titulo_grupo_e_resumo(self):
        ops = {o.nome: o for o in wsdl.interpretar(WSDL_EXEMPLO).operacoes}
        self.assertEqual(ops['entregarPeticao'].titulo, 'Entregar petição')
        self.assertEqual(ops['consultarClasses'].grupo, 'Tabelas de apoio')
        self.assertEqual(ops['fazerAlgoNovo'].titulo, 'Fazer algo novo')
        self.assertEqual(ops['fazerAlgoNovo'].grupo, 'Outras operações')
        self.assertEqual(
            ops['consultarProcesso'].resumo, 'Operação destinada a permitir a consulta a um processo judicial.'
        )

    def test_grupos_na_ordem_definida(self):
        grupos = [g for g, _ in wsdl.interpretar(WSDL_EXEMPLO).grupos]
        self.assertEqual(
            grupos, ['Processos', 'Comunicações processuais', 'Peticionamento', 'Tabelas de apoio', 'Outras operações']
        )

    def test_conteudo_invalido(self):
        for conteudo in (b'nao e xml', b'<html><body>erro</body></html>'):
            with self.assertRaises(wsdl.ErroWSDL):
                wsdl.interpretar(conteudo)

    def test_recusa_xml_com_entidades(self):
        bomba = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'
        with self.assertRaises(wsdl.ErroWSDL):
            wsdl.interpretar(bomba)


class DescreverServicoTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_usa_cache(self):
        with mock.patch.object(wsdl, '_baixar', return_value=WSDL_EXEMPLO) as baixar:
            wsdl.descrever_servico('https://x.jus.br/wsdl')
            wsdl.descrever_servico('https://x.jus.br/wsdl')
            self.assertEqual(baixar.call_count, 1)
            wsdl.descrever_servico('https://x.jus.br/wsdl', usar_cache=False)
            self.assertEqual(baixar.call_count, 2)

    def test_erro_de_rede_vira_erro_wsdl(self):
        with mock.patch('urllib.request.urlopen', side_effect=OSError('sem rede')):
            with self.assertRaises(wsdl.ErroWSDL):
                wsdl.descrever_servico('https://x.jus.br/wsdl')


class TelasSistemasTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('ana', email='ana@exemplo.com', password='x')
        self.client.force_login(self.user)
        self.eproc = SistemaJudicial.objects.get(nome='EPROC TJTO')
        self.amb1, self.amb2 = self.eproc.ambientes.order_by('ordem')

    def _servicos(self, ambiente, **kwargs):
        return reverse('sistemas:servicos', args=[ambiente.sistema_id, ambiente.pk])

    def test_exige_login(self):
        self.client.logout()
        for url in (reverse('sistemas:lista'), reverse('sistemas:ambientes', args=[self.eproc.pk]), self._servicos(self.amb1)):
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_lista_de_sistemas(self):
        resp = self.client.get(reverse('sistemas:lista'))
        self.assertContains(resp, 'EPROC TJTO')
        self.assertContains(resp, '2 ambientes de trabalho')
        self.assertContains(resp, reverse('sistemas:ambientes', args=[self.eproc.pk]))
        pje = SistemaJudicial.objects.get(nome='PJe')
        self.assertContains(resp, reverse('sistemas:ambientes', args=[pje.pk]))  # sem ambientes: abre para cadastrar o usuário
        self.assertContains(resp, 'Nenhum ambiente configurado')

    def test_lista_indica_que_a_senha_sera_solicitada(self):
        Credencial.objects.create(usuario=self.user, sistema=self.eproc, login='123')
        self.assertContains(self.client.get(reverse('sistemas:lista')), 'senha será solicitada')

    def test_sistema_inativo_some_e_da_404(self):
        self.eproc.ativo = False
        self.eproc.save()
        self.assertNotContains(self.client.get(reverse('sistemas:lista')), 'EPROC TJTO')
        self.assertEqual(self.client.get(reverse('sistemas:ambientes', args=[self.eproc.pk])).status_code, 404)
        self.assertEqual(self.client.get(self._servicos(self.amb1)).status_code, 404)

    def test_ambientes_lista_apenas_ativos(self):
        self.amb2.ativo = False
        self.amb2.save()
        resp = self.client.get(reverse('sistemas:ambientes', args=[self.eproc.pk]))
        self.assertContains(resp, '1º Grau')
        self.assertNotContains(resp, '2º Grau')
        self.assertContains(resp, 'Informe sua senha para acessar os serviços')
        self.assertEqual(self.client.get(self._servicos(self.amb2)).status_code, 404)

    def test_pagina_do_ambiente_guarda_sessao_e_nao_mostra_urls(self):
        resp = self.client.get(self._servicos(self.amb1))
        self.assertContains(resp, 'EPROC TJTO · 1º Grau')
        for texto in ('http://', 'https://', 'wsdl', 'eproc1g-ws'):
            self.assertNotContains(resp, texto)
        self.assertEqual(self.client.session['ambiente_id'], self.amb1.pk)
        resp = self.client.get(reverse('sistemas:ambientes', args=[self.eproc.pk]))
        self.assertContains(resp, 'em uso')
        for texto in ('http://', 'https://', 'wsdl', 'eproc1g-ws', 'eproc2g-ws'):
            self.assertNotContains(resp, texto)

    def test_pagina_do_ambiente_nao_lista_operacoes_nem_chama_o_tribunal(self):
        with mock.patch.object(wsdl, '_baixar') as baixar:
            resp = self.client.get(self._servicos(self.amb1))
        baixar.assert_not_called()
        self.assertNotContains(resp, 'consultarDocumentosProcesso')
        self.assertNotContains(resp, 'Entregar petição')

    def test_ambiente_de_outro_sistema_da_404(self):
        pje = SistemaJudicial.objects.get(nome='PJe')
        url = reverse('sistemas:servicos', args=[pje.pk, self.amb1.pk])
        self.assertEqual(self.client.get(url).status_code, 404)


class AdminAmbientesTests(TestCase):
    def test_paginas_do_admin(self):
        chefe = get_user_model().objects.create_superuser('chefe', email='chefe@exemplo.com', password='x')
        self.client.force_login(chefe)
        eproc = SistemaJudicial.objects.get(nome='EPROC TJTO')
        resp = self.client.get(reverse('admin:judicial_sistemajudicial_change', args=[eproc.pk]))
        self.assertContains(resp, URL_1G)
        for nome in ('admin:judicial_ambiente_changelist', 'admin:judicial_ambiente_add'):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 200)

    def test_admin_adiciona_ambiente_pelo_inline(self):
        chefe = get_user_model().objects.create_superuser('chefe', email='chefe@exemplo.com', password='x')
        self.client.force_login(chefe)
        pje = SistemaJudicial.objects.get(nome='PJe')
        resp = self.client.post(reverse('admin:judicial_sistemajudicial_change', args=[pje.pk]), {
            'nome': 'PJe', 'descricao': pje.descricao, 'url': '', 'ativo': 'on',
            'ambientes-TOTAL_FORMS': '1', 'ambientes-INITIAL_FORMS': '0',
            'ambientes-MIN_NUM_FORMS': '0', 'ambientes-MAX_NUM_FORMS': '1000',
            'ambientes-0-descricao': '1º Grau', 'ambientes-0-url_servico': 'https://pje1g.jus.br/ws?wsdl',
            'ambientes-0-ordem': '1', 'ambientes-0-ativo': 'on',
        })
        self.assertEqual(resp.status_code, 302, resp.content[:500])
        self.assertEqual(pje.ambientes.get().url_servico, 'https://pje1g.jus.br/ws?wsdl')
