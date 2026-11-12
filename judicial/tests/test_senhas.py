from hashlib import sha256
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from judicial import senhas
from judicial.models import Credencial, SistemaJudicial

SENHA = 'Minh@Senha-Do-Eproc'
HASH = sha256(SENHA.encode('utf-8')).hexdigest()


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('ana', email='ana@exemplo.com', password='x')
        self.client.force_login(self.user)
        self.eproc = SistemaJudicial.objects.get(nome='EPROC TJTO')
        self.url_senha = reverse('sistemas:informar_senha', args=[self.eproc.pk])

    def informar(self, login='12345678900', senha=SENHA, **extra):
        return self.client.post(self.url_senha, {'login': login, 'senha': senha, **extra})

    def hash_em_cache(self):
        chave = f'mni:senha:{self.user.pk}:{self.client.session.session_key}:{self.eproc.pk}'
        return cache.get(chave)


class InformarSenhaTests(Base):
    def test_guarda_somente_o_hash_sha256_em_cache(self):
        resp = self.informar()
        self.assertRedirects(resp, reverse('sistemas:ambientes', args=[self.eproc.pk]), fetch_redirect_response=False)
        self.assertEqual(self.hash_em_cache(), HASH)
        self.assertEqual(HASH, sha256(SENHA.encode('utf-8')).hexdigest())

    def test_senha_e_hash_nunca_vao_para_o_banco(self):
        self.informar()
        with connection.cursor() as c:
            for tabela in connection.introspection.table_names():
                colunas = [col.name for col in connection.introspection.get_table_description(c, tabela)]
                c.execute(f'SELECT * FROM {connection.ops.quote_name(tabela)}')
                for linha in c.fetchall():
                    texto = ' '.join(str(v) for v in linha)
                    self.assertNotIn(SENHA, texto, f'senha encontrada em {tabela}')
                    self.assertNotIn(HASH, texto, f'hash encontrado em {tabela} ({colunas})')

    def test_cria_ou_atualiza_o_login_da_credencial(self):
        self.informar(login=' 111 ')
        self.assertEqual(Credencial.objects.get(usuario=self.user, sistema=self.eproc).login, '111')
        self.informar(login='222')
        self.assertEqual(Credencial.objects.get(usuario=self.user, sistema=self.eproc).login, '222')

    def test_campos_vazios(self):
        for dados in ({'login': '', 'senha': SENHA}, {'login': '1', 'senha': ''}):
            resp = self.client.post(self.url_senha, dados, follow=True)
            self.assertContains(resp, 'Informe o usuário e a senha do EPROC TJTO')
        self.assertIsNone(self.hash_em_cache())

    def test_volta_para_a_pagina_de_origem_e_ignora_externa(self):
        origem = reverse('sistemas:servicos', args=[self.eproc.pk, self.eproc.ambientes.first().pk])
        self.assertRedirects(self.informar(next=origem), origem, fetch_redirect_response=False)
        resp = self.informar(next='https://malicioso.com/')
        self.assertRedirects(resp, reverse('sistemas:ambientes', args=[self.eproc.pk]), fetch_redirect_response=False)

    def test_exige_post_e_login(self):
        self.assertEqual(self.client.get(self.url_senha).status_code, 405)
        self.client.logout()
        self.assertEqual(self.informar().status_code, 302)
        self.assertIsNone(cache.get(f'mni:senha:{self.user.pk}:None:{self.eproc.pk}'))

    @override_settings(MNI_SENHA_TTL=123)
    def test_hash_expira_conforme_configuracao(self):
        with mock.patch.object(senhas.cache, 'set', wraps=senhas.cache.set) as cache_set:
            self.informar()
        self.assertTrue(all(c.args[2] == 123 for c in cache_set.call_args_list))


class CicloDeVidaTests(Base):
    def test_sair_do_jilo_apaga_as_senhas(self):
        self.informar()
        sessao = self.client.session.session_key
        self.client.post(reverse('logout'))
        self.assertIsNone(cache.get(f'mni:senha:{self.user.pk}:{sessao}:{self.eproc.pk}'))
        self.client.force_login(self.user)
        self.assertIsNone(self.hash_em_cache())

    def test_encerrar_acesso(self):
        self.informar()
        resp = self.client.post(reverse('sistemas:esquecer_senha', args=[self.eproc.pk]), follow=True)
        self.assertContains(resp, 'Acesso ao EPROC TJTO encerrado')
        self.assertIsNone(self.hash_em_cache())

    def test_senha_e_por_usuario_e_por_sessao(self):
        self.informar()
        outro = get_user_model().objects.create_user('beto', email='beto@exemplo.com', password='x')
        self.client.force_login(outro)  # nova sessão
        resp = self.client.get(reverse('sistemas:lista'))
        self.assertNotContains(resp, 'acesso liberado')

    def test_novo_login_pede_senha_de_novo(self):
        self.informar()
        self.client.logout()
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(reverse('sistemas:lista')), 'acesso liberado')


class TelasTests(Base):
    def test_lista_abre_modal_antes_e_libera_depois(self):
        resp = self.client.get(reverse('sistemas:lista'))
        self.assertContains(resp, 'x-on:click.prevent="aberto = true"')
        self.assertContains(resp, 'Acessar EPROC TJTO')
        self.assertContains(resp, 'senha será solicitada')
        self.informar()
        resp = self.client.get(reverse('sistemas:lista'))
        self.assertContains(resp, 'acesso liberado')
        self.assertNotContains(resp, 'Acessar EPROC TJTO')

    def test_modal_preenche_login_conhecido_e_nunca_a_senha(self):
        Credencial.objects.create(usuario=self.user, sistema=self.eproc, login='98765')
        resp = self.client.get(reverse('sistemas:lista'))
        self.assertContains(resp, 'value="98765"')
        self.informar(login='98765')
        self.client.post(reverse('sistemas:esquecer_senha', args=[self.eproc.pk]))
        resp = self.client.get(reverse('sistemas:ambientes', args=[self.eproc.pk]))
        self.assertNotContains(resp, SENHA)
        self.assertNotContains(resp, HASH)

    def test_ambientes_sem_senha_abre_modal(self):
        resp = self.client.get(reverse('sistemas:ambientes', args=[self.eproc.pk]))
        self.assertContains(resp, 'x-data="{ aberto: true }"')
        self.assertContains(resp, 'Informe sua senha para acessar os serviços')
        self.informar()
        resp = self.client.get(reverse('sistemas:ambientes', args=[self.eproc.pk]))
        self.assertContains(resp, 'x-data="{ aberto: false }"')
        self.assertNotContains(resp, 'Acessar EPROC TJTO')
