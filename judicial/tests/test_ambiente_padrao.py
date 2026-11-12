from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from judicial import wsdl
from judicial.models import Preferencia, SistemaJudicial
from judicial.preferencias import ambiente_padrao, definir_ambiente_padrao

from .test_sistemas import WSDL_EXEMPLO

SENHA = 'senha-forte-123'


class Base(TestCase):
    def setUp(self):
        cache.clear()
        patcher = mock.patch.object(wsdl, '_baixar', return_value=WSDL_EXEMPLO)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = get_user_model().objects.create_user('ana', email='ana@exemplo.com', password=SENHA)
        self.eproc = SistemaJudicial.objects.get(nome='EPROC TJTO')
        self.amb1, self.amb2 = self.eproc.ambientes.order_by('ordem')

    def url_servicos(self, amb):
        return reverse('sistemas:servicos', args=[amb.sistema_id, amb.pk])

    def login(self, **extra):
        return self.client.post(reverse('login') + extra.pop('query', ''), {'username': 'ana', 'password': SENHA, **extra})


class DefinirPadraoTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_definir_e_trocar_padrao(self):
        url = reverse('sistemas:definir_padrao', args=[self.eproc.pk, self.amb1.pk])
        resp = self.client.post(url, follow=True)
        self.assertRedirects(resp, self.url_servicos(self.amb1))
        self.assertContains(resp, 'agora é o seu ambiente padrão')
        self.assertContains(resp, 'ambiente padrão')
        self.assertEqual(ambiente_padrao(self.user), self.amb1)

        self.client.post(reverse('sistemas:definir_padrao', args=[self.eproc.pk, self.amb2.pk]))
        self.assertEqual(ambiente_padrao(self.user), self.amb2)
        self.assertEqual(Preferencia.objects.count(), 1)

    def test_definir_exige_post(self):
        url = reverse('sistemas:definir_padrao', args=[self.eproc.pk, self.amb1.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertIsNone(ambiente_padrao(self.user))

    def test_nao_define_ambiente_inativo_ou_de_outro_sistema(self):
        pje = SistemaJudicial.objects.get(nome='PJe')
        self.assertEqual(self.client.post(reverse('sistemas:definir_padrao', args=[pje.pk, self.amb1.pk])).status_code, 404)
        self.amb2.ativo = False
        self.amb2.save()
        self.assertEqual(self.client.post(reverse('sistemas:definir_padrao', args=[self.eproc.pk, self.amb2.pk])).status_code, 404)

    def test_remover_padrao(self):
        definir_ambiente_padrao(self.user, self.amb1)
        resp = self.client.post(reverse('sistemas:remover_padrao'), {'next': self.url_servicos(self.amb1)})
        self.assertRedirects(resp, self.url_servicos(self.amb1))
        self.assertIsNone(ambiente_padrao(self.user))

    def test_remover_ignora_next_externo(self):
        definir_ambiente_padrao(self.user, self.amb1)
        resp = self.client.post(reverse('sistemas:remover_padrao'), {'next': 'https://malicioso.com/'})
        self.assertRedirects(resp, reverse('sistemas:lista'))

    def test_marcacoes_nas_telas(self):
        definir_ambiente_padrao(self.user, self.amb1)
        self.assertContains(self.client.get(reverse('sistemas:lista')), '★ padrão: 1º Grau')
        self.assertContains(self.client.get(reverse('sistemas:ambientes', args=[self.eproc.pk])), '★ padrão')
        self.assertContains(self.client.get(reverse('inicio')), 'Ir para o ambiente padrão')
        self.assertContains(self.client.get(self.url_servicos(self.amb1)), 'Padrão · remover')
        self.assertContains(self.client.get(self.url_servicos(self.amb2)), 'Definir como padrão')

    def test_padrao_e_individual(self):
        outro = get_user_model().objects.create_user('beto', email='beto@exemplo.com', password='x')
        definir_ambiente_padrao(self.user, self.amb1)
        self.assertIsNone(ambiente_padrao(outro))


class LoginRedirecionaTests(Base):
    def test_sem_padrao_vai_para_inicio(self):
        self.assertRedirects(self.login(), reverse('inicio'))

    def test_com_padrao_vai_direto_para_o_ambiente(self):
        definir_ambiente_padrao(self.user, self.amb1)
        resp = self.login()
        self.assertRedirects(resp, self.url_servicos(self.amb1))
        self.assertEqual(self.client.session['ambiente_id'], self.amb1.pk)

    def test_abrir_o_site_e_logar_vai_para_o_padrao(self):
        # Acessar "/" sem login gera ?next=/ — não deve impedir o ambiente padrão.
        definir_ambiente_padrao(self.user, self.amb1)
        self.assertRedirects(self.client.get('/'), f"{reverse('login')}?next=/")
        resp = self.login(next='/', query='?next=/')
        self.assertRedirects(resp, self.url_servicos(self.amb1))

    def test_next_especifico_tem_prioridade(self):
        definir_ambiente_padrao(self.user, self.amb1)
        destino = reverse('judicial:lista')
        resp = self.login(next=destino, query=f'?next={destino}')
        self.assertRedirects(resp, destino)

    def test_next_externo_e_ignorado(self):
        definir_ambiente_padrao(self.user, self.amb1)
        resp = self.login(next='https://malicioso.com/')
        self.assertRedirects(resp, self.url_servicos(self.amb1))

    def test_padrao_desativado_volta_para_inicio(self):
        definir_ambiente_padrao(self.user, self.amb1)
        self.amb1.ativo = False
        self.amb1.save()
        self.assertRedirects(self.login(), reverse('inicio'))

    def test_sistema_desativado_volta_para_inicio(self):
        definir_ambiente_padrao(self.user, self.amb1)
        self.eproc.ativo = False
        self.eproc.save()
        self.assertRedirects(self.login(), reverse('inicio'))

    def test_ambiente_excluido_limpa_padrao(self):
        definir_ambiente_padrao(self.user, self.amb1)
        self.amb1.delete()
        self.assertIsNone(Preferencia.objects.get(usuario=self.user).ambiente_padrao)
        self.assertRedirects(self.login(), reverse('inicio'))

    def test_pagina_inicial_continua_acessivel(self):
        definir_ambiente_padrao(self.user, self.amb1)
        self.login()
        self.assertEqual(self.client.get(reverse('inicio')).status_code, 200)

    def test_login_por_email_tambem_redireciona(self):
        definir_ambiente_padrao(self.user, self.amb1)
        resp = self.client.post(reverse('login'), {'username': 'ana@exemplo.com', 'password': SENHA})
        self.assertRedirects(resp, self.url_servicos(self.amb1))
