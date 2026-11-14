from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from judicial.models import Credencial, SistemaJudicial


class Base(TestCase):
    def setUp(self):
        User = get_user_model()
        self.ana = User.objects.create_user('ana', email='ana@exemplo.com', password='x')
        self.beto = User.objects.create_user('beto', email='beto@exemplo.com', password='x')
        self.eproc = SistemaJudicial.objects.get(nome='EPROC TJTO')
        self.pje = SistemaJudicial.objects.get(nome='PJe')
        self.client.force_login(self.ana)


class SistemasIniciaisTests(TestCase):
    def test_migracao_cria_sistemas_iniciais(self):
        self.assertLessEqual(
            {'EPROC TJTO', 'PROJUD', 'PJe'}, set(SistemaJudicial.objects.values_list('nome', flat=True))
        )

    def test_migracao_cria_sistemas_confirmados(self):
        nomes = set(SistemaJudicial.objects.values_list('nome', flat=True))
        self.assertLessEqual({'STF', 'PJE TJBA', 'PJE TRF1', 'PJE-JT TRT15', 'EPROC TJM-RS'}, nomes)
        self.assertEqual(SistemaJudicial.objects.get(nome='PJE TJMA').ambientes.count(), 2)
        # Webservice do Projudi do TJAM não é MNI: fica cadastrado, mas inativo.
        self.assertFalse(SistemaJudicial.objects.get(nome='PROJUDI TJAM').ativo)

    def test_nome_do_sistema_unico_sem_diferenciar_maiusculas(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SistemaJudicial.objects.create(nome='eproc tjto')


class CredencialTests(Base):
    """O usuário de cada sistema é gerenciado na página do próprio sistema."""

    def url(self, sistema=None):
        return reverse('sistemas:credencial', args=[(sistema or self.eproc).pk])

    def url_excluir(self, sistema=None):
        return reverse('sistemas:excluir_credencial', args=[(sistema or self.eproc).pk])

    def url_sistema(self, sistema=None):
        return reverse('sistemas:ambientes', args=[(sistema or self.eproc).pk])

    def test_exige_login(self):
        self.client.logout()
        resp = self.client.get(self.url())
        self.assertRedirects(resp, f"{reverse('login')}?next={self.url()}")

    def test_pagina_do_sistema_mostra_cadastro_quando_nao_ha_usuario(self):
        resp = self.client.get(self.url_sistema())
        self.assertContains(resp, 'Nenhum usuário cadastrado')
        self.assertContains(resp, self.url())

    def test_pagina_do_sistema_mostra_o_usuario(self):
        Credencial.objects.create(usuario=self.ana, sistema=self.eproc, login='12345678900')
        resp = self.client.get(self.url_sistema())
        self.assertContains(resp, '12345678900')
        self.assertContains(resp, self.url_excluir())

    def test_cadastrar(self):
        resp = self.client.post(self.url(), {'login': '  12345678900 '}, follow=True)
        self.assertRedirects(resp, self.url_sistema())
        c = Credencial.objects.get()
        self.assertEqual((c.usuario, c.sistema, c.login), (self.ana, self.eproc, '12345678900'))
        self.assertContains(resp, 'Usuário cadastrado.')

    def test_login_obrigatorio(self):
        resp = self.client.post(self.url(), {'login': '  '})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Credencial.objects.count(), 0)

    def test_um_usuario_por_sistema(self):
        Credencial.objects.create(usuario=self.ana, sistema=self.eproc, login='antigo')
        self.client.post(self.url(), {'login': 'novo'})
        self.assertEqual(list(Credencial.objects.values_list('login', flat=True)), ['novo'])

    def test_alterar_mantem_os_outros_sistemas(self):
        Credencial.objects.create(usuario=self.ana, sistema=self.pje, login='pje')
        Credencial.objects.create(usuario=self.ana, sistema=self.eproc, login='antigo')
        resp = self.client.post(self.url(), {'login': 'novo'}, follow=True)
        self.assertContains(resp, 'Usuário atualizado.')
        self.assertEqual(Credencial.objects.get(sistema=self.pje).login, 'pje')

    def test_usuarios_diferentes_podem_usar_o_mesmo_sistema(self):
        Credencial.objects.create(usuario=self.beto, sistema=self.eproc, login='beto')
        self.client.post(self.url(), {'login': 'ana'})
        self.assertEqual(Credencial.objects.get(usuario=self.beto).login, 'beto')
        self.assertEqual(Credencial.objects.get(usuario=self.ana).login, 'ana')

    def test_sistema_inativo_nao_aceita_cadastro(self):
        self.pje.ativo = False
        self.pje.save()
        self.assertEqual(self.client.post(self.url(self.pje), {'login': 'x'}).status_code, 404)
        self.assertFalse(Credencial.objects.exists())

    def test_formulario_nao_tem_campo_de_senha(self):
        campos = {f.name for f in Credencial._meta.get_fields()}
        self.assertFalse(any('senha' in c for c in campos))
        self.assertNotContains(self.client.get(self.url()), 'type="password"')

    def test_excluir(self):
        Credencial.objects.create(usuario=self.ana, sistema=self.eproc, login='a')
        self.assertContains(self.client.get(self.url_excluir()), 'Tem certeza')
        resp = self.client.post(self.url_excluir())
        self.assertRedirects(resp, self.url_sistema())
        self.assertFalse(Credencial.objects.exists())

    def test_excluir_sem_usuario_cadastrado(self):
        self.assertEqual(self.client.get(self.url_excluir()).status_code, 404)

    def test_nao_altera_nem_exclui_o_usuario_de_outra_pessoa(self):
        Credencial.objects.create(usuario=self.beto, sistema=self.eproc, login='beto')
        self.assertNotContains(self.client.get(self.url_sistema()), 'beto')
        self.assertEqual(self.client.post(self.url_excluir()).status_code, 404)
        self.client.post(self.url(), {'login': 'ana'})
        self.assertEqual(Credencial.objects.get(usuario=self.beto).login, 'beto')

    def test_lista_de_sistemas_mostra_o_usuario(self):
        Credencial.objects.create(usuario=self.ana, sistema=self.eproc, login='12345678900')
        resp = self.client.get(reverse('sistemas:lista'))
        self.assertContains(resp, '12345678900')
        self.assertContains(resp, 'Usuário não cadastrado')  # demais sistemas

    def test_area_separada_de_credenciais_nao_existe_mais(self):
        self.assertEqual(self.client.get('/credenciais/').status_code, 404)
        self.assertNotContains(self.client.get(reverse('inicio')), 'Credenciais judiciais')

    def test_sistema_com_credenciais_nao_pode_ser_apagado(self):
        from django.db.models import ProtectedError

        Credencial.objects.create(usuario=self.ana, sistema=self.eproc, login='a')
        with self.assertRaises(ProtectedError):
            self.eproc.delete()

    def test_excluir_usuario_remove_credenciais(self):
        Credencial.objects.create(usuario=self.ana, sistema=self.eproc, login='a')
        self.ana.delete()
        self.assertFalse(Credencial.objects.exists())


class AdminJudicialTests(TestCase):
    def test_paginas_do_admin(self):
        chefe = get_user_model().objects.create_superuser('chefe', email='chefe@exemplo.com', password='x')
        self.client.force_login(chefe)
        for url in ('judicial_sistemajudicial', 'judicial_ambiente'):
            self.assertEqual(self.client.get(reverse(f'admin:{url}_changelist')).status_code, 200)
            self.assertEqual(self.client.get(reverse(f'admin:{url}_add')).status_code, 200)
