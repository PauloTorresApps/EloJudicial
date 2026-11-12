"""Isolamento entre usuários: ninguém vê nem usa a credencial, a senha ou as preferências de outro.

Ana e Beto ficam logados ao mesmo tempo, em sessões separadas. Beto tem usuário e senha
liberada no EPROC e um ambiente padrão; nada disso pode aparecer ou ser usado por Ana.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from judicial import mni, senhas
from judicial.models import Credencial, Preferencia, SistemaJudicial
from judicial.preferencias import definir_ambiente_padrao

LOGIN_BETO = 'login-secreto-do-beto'
SENHA_BETO = 'senha-do-beto'
SENHA_ANA = 'senha-da-ana'


class IsolamentoTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.ana = User.objects.create_user('ana', email='ana@exemplo.com', password='x')
        self.beto = User.objects.create_user('beto', email='beto@exemplo.com', password='x')
        self.eproc = SistemaJudicial.objects.get(nome='EPROC TJTO')
        self.pje = SistemaJudicial.objects.get(nome='PJe')
        self.amb = self.eproc.ambientes.order_by('ordem').first()

        self.client.force_login(self.ana)
        self.cliente_beto = Client()
        self.cliente_beto.force_login(self.beto)
        self.cliente_beto.post(
            reverse('sistemas:informar_senha', args=[self.eproc.pk]), {'login': LOGIN_BETO, 'senha': SENHA_BETO}
        )
        definir_ambiente_padrao(self.beto, self.amb)
        self.assertEqual(Credencial.objects.get(usuario=self.beto).login, LOGIN_BETO)

    # URLs com dados do EPROC (o sistema em que só o Beto tem credencial)
    def urls_do_eproc(self):
        args = [self.eproc.pk, self.amb.pk]
        return [
            reverse('inicio'),
            reverse('sistemas:lista'),
            reverse('sistemas:ambientes', args=[self.eproc.pk]),
            reverse('sistemas:credencial', args=[self.eproc.pk]),
            reverse('sistemas:servicos', args=args),
            reverse('sistemas:avisos', args=args),
            reverse('sistemas:processo', args=[*args, '00012341420248272729']),
            reverse('sistemas:processo_dados', args=[*args, '00012341420248272729']),
        ]

    def test_nenhuma_pagina_mostra_o_login_de_outro_usuario(self):
        for url in self.urls_do_eproc():
            with self.subTest(url=url):
                self.assertNotContains(self.client.get(url), LOGIN_BETO)

    def test_inicio_mostra_somente_os_sistemas_do_proprio_usuario(self):
        resp = self.client.get(reverse('inicio'))
        self.assertContains(resp, 'Nenhum sistema configurado ainda')
        self.assertNotContains(resp, 'EPROC TJTO')

        Credencial.objects.create(usuario=self.ana, sistema=self.pje, login='ana-pje')
        resp = self.client.get(reverse('inicio'))
        self.assertContains(resp, 'ana-pje')
        self.assertNotContains(resp, 'EPROC TJTO')
        self.assertContains(self.cliente_beto.get(reverse('inicio')), 'EPROC TJTO')
        self.assertNotContains(self.cliente_beto.get(reverse('inicio')), 'ana-pje')

    def test_senha_liberada_de_outro_usuario_nao_vale(self):
        resp = self.client.get(reverse('sistemas:lista'))
        self.assertNotContains(resp, 'acesso liberado')
        self.assertContains(self.client.get(reverse('sistemas:ambientes', args=[self.eproc.pk])), 'Informe sua senha')

    def test_consultas_ao_tribunal_nunca_usam_dados_de_outro_usuario(self):
        args = [self.eproc.pk, self.amb.pk]
        with mock.patch.object(mni, 'consultar_avisos_pendentes', side_effect=mni.ErroMNI('x')) as avisos, \
                mock.patch.object(mni, 'consultar_processo', side_effect=mni.ErroMNI('x')) as processo, \
                mock.patch.object(mni, 'consultar_documento', side_effect=mni.ErroMNI('x')) as documento:
            # Sem credencial própria: nada é enviado ao tribunal.
            self.client.get(reverse('sistemas:avisos', args=args))
            self.client.get(reverse('sistemas:processo_dados', args=[*args, '00012341420248272729']))
            self.client.get(reverse('sistemas:documento', args=[*args, '00012341420248272729', '1']))
            self.assertFalse(avisos.called or processo.called or documento.called)

            # Com credencial e senha próprias: vão só os dados da Ana.
            self.client.post(
                reverse('sistemas:informar_senha', args=[self.eproc.pk]), {'login': 'ana', 'senha': SENHA_ANA}
            )
            self.client.get(reverse('sistemas:avisos', args=args))
            self.client.get(reverse('sistemas:processo_dados', args=[*args, '00012341420248272729']))
            self.client.get(reverse('sistemas:documento', args=[*args, '00012341420248272729', '1']))
        for chamada in (avisos, processo, documento):
            _, login, senha_hash, *_ = chamada.call_args.args
            self.assertEqual((login, senha_hash), ('ana', senhas.gerar_hash(SENHA_ANA)))

    def test_nao_altera_nem_remove_a_credencial_de_outro_usuario(self):
        self.assertEqual(self.client.get(reverse('sistemas:excluir_credencial', args=[self.eproc.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('sistemas:excluir_credencial', args=[self.eproc.pk])).status_code, 404)
        self.client.post(reverse('sistemas:credencial', args=[self.eproc.pk]), {'login': 'ana'})
        self.client.post(reverse('sistemas:informar_senha', args=[self.eproc.pk]), {'login': 'ana2', 'senha': 'y'})
        self.assertEqual(Credencial.objects.get(usuario=self.beto).login, LOGIN_BETO)
        self.assertEqual(Credencial.objects.get(usuario=self.ana).login, 'ana2')

    def test_encerrar_acesso_nao_afeta_a_senha_de_outro_usuario(self):
        self.client.post(reverse('sistemas:esquecer_senha', args=[self.eproc.pk]))
        self.client.post(reverse('sistemas:excluir_credencial', args=[self.eproc.pk]))
        self.client.logout()
        resp = self.cliente_beto.get(reverse('sistemas:lista'))
        self.assertContains(resp, 'acesso liberado')

    def test_preferencias_sao_independentes(self):
        self.client.post(reverse('sistemas:remover_padrao'))
        self.assertEqual(Preferencia.objects.get(usuario=self.beto).ambiente_padrao, self.amb)
        resp = self.client.get(reverse('sistemas:lista'))
        self.assertNotContains(resp, '★ padrão')

    def test_paginas_de_usuario_logado_nao_ficam_em_cache(self):
        for url in self.urls_do_eproc():
            with self.subTest(url=url):
                cache_control = self.client.get(url).get('Cache-Control', '')
                for diretiva in ('private', 'no-store', 'no-cache'):
                    self.assertIn(diretiva, cache_control)


class AdministracaoNaoVeCredenciaisTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.beto = User.objects.create_user('beto', email='beto@exemplo.com', password='x')
        self.eproc = SistemaJudicial.objects.get(nome='EPROC TJTO')
        Credencial.objects.create(usuario=self.beto, sistema=self.eproc, login=LOGIN_BETO)
        definir_ambiente_padrao(self.beto, self.eproc.ambientes.first())
        self.client.force_login(User.objects.create_superuser('chefe', email='chefe@exemplo.com', password='x'))

    def test_credenciais_e_preferencias_fora_do_admin(self):
        for url in ('/admin/judicial/credencial/', '/admin/judicial/preferencia/'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        self.assertNotContains(self.client.get('/admin/'), 'Credenciais')

    def test_telas_de_exclusao_nao_mostram_o_login(self):
        telas = [
            reverse('admin:auth_user_delete', args=[self.beto.pk]),
            reverse('admin:judicial_sistemajudicial_delete', args=[self.eproc.pk]),
            reverse('admin:judicial_sistemajudicial_changelist'),
        ]
        for url in telas:
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200)
                self.assertNotContains(resp, LOGIN_BETO)
