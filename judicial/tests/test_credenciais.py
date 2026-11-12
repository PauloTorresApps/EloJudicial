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
        self.assertEqual(
            set(SistemaJudicial.objects.values_list('nome', flat=True)), {'EPROC TJTO', 'PROJUD', 'PJe'}
        )

    def test_nome_do_sistema_unico_sem_diferenciar_maiusculas(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SistemaJudicial.objects.create(nome='eproc tjto')


class CredencialTests(Base):
    def test_lista_exige_login(self):
        self.client.logout()
        resp = self.client.get(reverse('judicial:lista'))
        self.assertRedirects(resp, f"{reverse('login')}?next={reverse('judicial:lista')}")

    def test_lista_vazia(self):
        resp = self.client.get(reverse('judicial:lista'))
        self.assertContains(resp, 'Nenhuma credencial cadastrada')

    def test_criar_credencial(self):
        resp = self.client.post(
            reverse('judicial:criar'), {'sistema': self.eproc.pk, 'login': '  12345678900 '}, follow=True
        )
        self.assertRedirects(resp, reverse('judicial:lista'))
        c = Credencial.objects.get()
        self.assertEqual((c.usuario, c.sistema, c.login), (self.ana, self.eproc, '12345678900'))
        self.assertContains(resp, '12345678900')
        self.assertContains(resp, 'Credencial cadastrada.')

    def test_campos_obrigatorios(self):
        resp = self.client.post(reverse('judicial:criar'), {})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Credencial.objects.count(), 0)

    def test_nao_permite_duas_credenciais_no_mesmo_sistema(self):
        Credencial.objects.create(usuario=self.ana, sistema=self.eproc, login='a')
        resp = self.client.post(reverse('judicial:criar'), {'sistema': self.eproc.pk, 'login': 'b'})
        self.assertContains(resp, 'Você já tem uma credencial cadastrada para este sistema.')
        self.assertEqual(Credencial.objects.count(), 1)

    def test_usuarios_diferentes_podem_usar_o_mesmo_sistema(self):
        Credencial.objects.create(usuario=self.beto, sistema=self.eproc, login='beto')
        resp = self.client.post(reverse('judicial:criar'), {'sistema': self.eproc.pk, 'login': 'ana'})
        self.assertRedirects(resp, reverse('judicial:lista'))

    def test_sistema_inativo_nao_aparece_para_cadastro(self):
        self.pje.ativo = False
        self.pje.save()
        resp = self.client.post(reverse('judicial:criar'), {'sistema': self.pje.pk, 'login': 'x'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Credencial.objects.count(), 0)
        self.assertNotContains(self.client.get(reverse('judicial:criar')), '>PJe<')

    def test_credencial_nao_tem_campo_de_senha(self):
        campos = {f.name for f in Credencial._meta.get_fields()}
        self.assertFalse(any('senha' in c for c in campos))
        self.assertNotContains(self.client.get(reverse('judicial:criar')), 'type="password"')

    def test_editar_credencial(self):
        c = Credencial.objects.create(usuario=self.ana, sistema=self.eproc, login='antigo')
        resp = self.client.post(reverse('judicial:editar', args=[c.pk]), {'sistema': self.eproc.pk, 'login': 'novo'})
        self.assertRedirects(resp, reverse('judicial:lista'))
        c.refresh_from_db()
        self.assertEqual(c.login, 'novo')

    def test_editar_mantem_sistema_que_foi_desativado(self):
        c = Credencial.objects.create(usuario=self.ana, sistema=self.pje, login='antigo')
        self.pje.ativo = False
        self.pje.save()
        resp = self.client.post(reverse('judicial:editar', args=[c.pk]), {'sistema': self.pje.pk, 'login': 'novo'})
        self.assertRedirects(resp, reverse('judicial:lista'))

    def test_excluir_credencial(self):
        c = Credencial.objects.create(usuario=self.ana, sistema=self.eproc, login='a')
        self.assertContains(self.client.get(reverse('judicial:excluir', args=[c.pk])), 'Tem certeza')
        resp = self.client.post(reverse('judicial:excluir', args=[c.pk]))
        self.assertRedirects(resp, reverse('judicial:lista'))
        self.assertFalse(Credencial.objects.exists())

    def test_nao_acessa_credencial_de_outro_usuario(self):
        c = Credencial.objects.create(usuario=self.beto, sistema=self.eproc, login='beto')
        self.assertNotContains(self.client.get(reverse('judicial:lista')), 'beto')
        for nome in ('judicial:editar', 'judicial:excluir'):
            self.assertEqual(self.client.get(reverse(nome, args=[c.pk])).status_code, 404)
            self.assertEqual(self.client.post(reverse(nome, args=[c.pk]), {'sistema': self.eproc.pk, 'login': 'hack'}).status_code, 404)
        c.refresh_from_db()
        self.assertEqual(c.login, 'beto')

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
        for url in ('judicial_sistemajudicial', 'judicial_credencial'):
            self.assertEqual(self.client.get(reverse(f'admin:{url}_changelist')).status_code, 200)
            self.assertEqual(self.client.get(reverse(f'admin:{url}_add')).status_code, 200)


class RetornoCredencialTests(Base):
    def test_cadastro_volta_para_a_pagina_de_origem(self):
        origem = reverse('sistemas:ambientes', args=[self.eproc.pk])
        resp = self.client.post(
            reverse('judicial:criar') + f'?next={origem}',
            {'sistema': self.eproc.pk, 'login': 'x', 'next': origem},
        )
        self.assertRedirects(resp, origem)

    def test_next_externo_e_ignorado(self):
        resp = self.client.post(
            reverse('judicial:criar'), {'sistema': self.eproc.pk, 'login': 'x', 'next': 'https://mal.com/'}
        )
        self.assertRedirects(resp, reverse('judicial:lista'))

    def test_sistema_pre_selecionado(self):
        resp = self.client.get(reverse('judicial:criar') + f'?sistema={self.eproc.pk}')
        self.assertContains(resp, f'<option value="{self.eproc.pk}" selected>')
