from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class AreaUsuarioTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('maria', password='senha-forte-123')

    def test_anonimo_redireciona_para_login(self):
        resp = self.client.get(reverse('inicio'))
        self.assertRedirects(resp, f"{reverse('login')}?next=/")

    def test_pagina_login_acessivel(self):
        self.assertEqual(self.client.get(reverse('login')).status_code, 200)

    def test_login_e_inicio(self):
        resp = self.client.post(reverse('login'), {'username': 'maria', 'password': 'senha-forte-123'})
        self.assertRedirects(resp, reverse('inicio'))
        self.assertContains(self.client.get(reverse('inicio')), 'Bem-vindo(a), maria')

    def test_usuario_comum_nao_acessa_admin(self):
        self.client.force_login(self.user)
        resp = self.client.get('/admin/')
        self.assertEqual(resp.status_code, 302)

    def test_logout(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse('logout'))
        self.assertRedirects(resp, reverse('login'))


class CadastroTests(TestCase):
    dados = {
        'first_name': 'João',
        'last_name': 'Silva',
        'email': 'Joao@Exemplo.com',
        'username': 'joao',
        'password1': 'uma-senha-bem-forte-42',
        'password2': 'uma-senha-bem-forte-42',
    }

    def test_pagina_cadastro_publica(self):
        self.assertEqual(self.client.get(reverse('cadastro')).status_code, 200)

    def test_cadastro_cria_usuario_inativo_e_envia_email(self):
        from django.core import mail

        resp = self.client.post(reverse('cadastro'), self.dados)
        self.assertRedirects(resp, reverse('cadastro_enviado'))
        user = get_user_model().objects.get(username='joao')
        self.assertEqual(user.email, 'joao@exemplo.com')
        self.assertFalse(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['joao@exemplo.com'])
        self.assertEqual(mail.outbox[0].subject, 'Confirme seu cadastro - Jilo')

    def test_email_duplicado_rejeitado(self):
        get_user_model().objects.create_user('outro', email='joao@exemplo.com', password='x')
        resp = self.client.post(reverse('cadastro'), self.dados)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Já existe um usuário cadastrado com este e-mail.')
        self.assertFalse(get_user_model().objects.filter(username='joao').exists())

    def test_senhas_diferentes_rejeitadas(self):
        resp = self.client.post(reverse('cadastro'), {**self.dados, 'password2': 'outra-coisa-99'})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username='joao').exists())

    def test_usuario_logado_e_redirecionado(self):
        user = get_user_model().objects.create_user('ana', password='x')
        self.client.force_login(user)
        self.assertRedirects(self.client.get(reverse('cadastro')), reverse('inicio'))


class RecuperacaoSenhaTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'carla', email='carla@exemplo.com', password='senha-antiga-123', first_name='Carla'
        )

    def test_envia_email_e_redefine_senha(self):
        from django.core import mail
        import re

        resp = self.client.post(reverse('password_reset'), {'email': 'carla@exemplo.com'})
        self.assertRedirects(resp, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.subject, 'Recuperação de senha - Jilo')
        self.assertIn('Olá, Carla', msg.body)

        link = re.search(r'http://testserver(\S+)', msg.body).group(1)
        resp = self.client.get(link, follow=True)
        self.assertContains(resp, 'Criar nova senha')
        resp = self.client.post(resp.redirect_chain[-1][0], {
            'new_password1': 'senha-nova-bem-forte-7',
            'new_password2': 'senha-nova-bem-forte-7',
        })
        self.assertRedirects(resp, reverse('password_reset_complete'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('senha-nova-bem-forte-7'))

    def test_email_inexistente_nao_envia(self):
        from django.core import mail

        self.client.post(reverse('password_reset'), {'email': 'ninguem@exemplo.com'})
        self.assertEqual(len(mail.outbox), 0)


class ConfirmacaoEmailTests(TestCase):
    dados = CadastroTests.dados

    def _cadastrar_e_pegar_link(self):
        from django.core import mail
        import re

        self.client.post(reverse('cadastro'), self.dados)
        return re.search(r'http://testserver(\S+)', mail.outbox[-1].body).group(1)

    def test_nao_entra_antes_de_confirmar(self):
        self.client.post(reverse('cadastro'), self.dados)
        resp = self.client.post(reverse('login'), {'username': 'joao', 'password': self.dados['password1']})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Reenviar confirmação')

    def test_link_ativa_conta_e_loga(self):
        link = self._cadastrar_e_pegar_link()
        resp = self.client.get(link, follow=True)
        self.assertRedirects(resp, reverse('inicio'))
        self.assertContains(resp, 'E-mail confirmado! Sua conta está ativa.')
        self.assertTrue(get_user_model().objects.get(username='joao').is_active)

    def test_link_nao_pode_ser_reutilizado(self):
        link = self._cadastrar_e_pegar_link()
        self.client.get(link)
        self.client.logout()
        resp = self.client.get(link)
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, 'Link inválido ou expirado', status_code=400)

    def test_link_adulterado_invalido(self):
        link = self._cadastrar_e_pegar_link()
        resp = self.client.get(link.rstrip('/') + 'x/')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(get_user_model().objects.get(username='joao').is_active)

    def test_token_de_reset_de_senha_nao_ativa_conta(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        self.client.post(reverse('cadastro'), self.dados)
        user = get_user_model().objects.get(username='joao')
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        resp = self.client.get(reverse('ativar_conta', args=[uid, default_token_generator.make_token(user)]))
        self.assertEqual(resp.status_code, 400)

    def test_reenviar_confirmacao(self):
        from django.core import mail

        self.client.post(reverse('cadastro'), self.dados)
        resp = self.client.post(reverse('reenviar_confirmacao'), {'email': 'JOAO@exemplo.com'})
        self.assertRedirects(resp, reverse('cadastro_enviado'))
        self.assertEqual(len(mail.outbox), 2)

    def test_reenviar_nao_envia_para_conta_ativa_ou_inexistente(self):
        from django.core import mail

        get_user_model().objects.create_user('ativo', email='ativo@exemplo.com', password='x')
        for email in ('ativo@exemplo.com', 'ninguem@exemplo.com'):
            resp = self.client.post(reverse('reenviar_confirmacao'), {'email': email})
            self.assertRedirects(resp, reverse('cadastro_enviado'))
        self.assertEqual(len(mail.outbox), 0)


class LoginPorEmailTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'paula', email='paula@exemplo.com', password='senha-forte-123'
        )

    def _login(self, ident, senha='senha-forte-123'):
        return self.client.post(reverse('login'), {'username': ident, 'password': senha})

    def test_login_por_usuario(self):
        self.assertRedirects(self._login('paula'), reverse('inicio'))

    def test_login_por_email(self):
        self.assertRedirects(self._login('paula@exemplo.com'), reverse('inicio'))

    def test_login_por_email_ignora_maiusculas_e_espacos(self):
        self.assertRedirects(self._login('  Paula@Exemplo.COM '), reverse('inicio'))

    def test_senha_errada_por_email(self):
        resp = self._login('paula@exemplo.com', 'errada')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Usuário, e-mail ou senha incorretos.')

    def test_email_de_conta_inativa_nao_entra(self):
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self._login('paula@exemplo.com').status_code, 200)

    def test_email_duplicado_nao_entra_por_email_mas_entra_por_usuario(self):
        # O banco já impede e-mails repetidos; aqui simulamos dados legados.
        from unittest import mock

        User = get_user_model()
        original_get = User._default_manager.get

        def get_simulando_duplicado(*args, **kwargs):
            if 'email__iexact' in kwargs:
                raise User.MultipleObjectsReturned
            return original_get(*args, **kwargs)

        with mock.patch.object(User._default_manager, 'get', side_effect=get_simulando_duplicado):
            self.assertEqual(self._login('paula@exemplo.com').status_code, 200)
        self.assertRedirects(self._login('paula'), reverse('inicio'))

    def test_login_por_email_no_admin(self):
        get_user_model().objects.create_superuser('chefe', email='chefe@exemplo.com', password='senha-forte-123')
        resp = self.client.post('/admin/login/?next=/admin/', {
            'username': 'chefe@exemplo.com', 'password': 'senha-forte-123', 'next': '/admin/',
        })
        self.assertRedirects(resp, '/admin/')

    def test_cadastro_recusa_usuario_com_arroba(self):
        dados = {**CadastroTests.dados, 'username': 'alguem@exemplo.com'}
        resp = self.client.post(reverse('cadastro'), dados)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'O nome de usuário não pode conter')

    def test_sessao_continua_valida_apos_ativacao(self):
        # login() sem backend explícito precisa gravar o backend configurado
        self.client.post(reverse('cadastro'), CadastroTests.dados)
        from django.core import mail
        import re
        link = re.search(r'http://testserver(\S+)', mail.outbox[-1].body).group(1)
        self.client.get(link)
        self.assertEqual(self.client.get(reverse('inicio')).status_code, 200)


class EmailUnicoNoBancoTests(TestCase):
    def test_banco_recusa_email_repetido_sem_diferenciar_maiusculas(self):
        from django.db import IntegrityError, transaction

        User = get_user_model()
        User.objects.create_user('a', email='repetido@exemplo.com', password='x')
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user('b', email='REPETIDO@Exemplo.com', password='x')

    def test_banco_permite_varios_usuarios_sem_email(self):
        User = get_user_model()
        User.objects.create_user('sem1', password='x')
        User.objects.create_user('sem2', password='x')
        self.assertEqual(User.objects.filter(email='').count(), 2)

    def test_cadastro_concorrente_mostra_erro_em_vez_de_500(self):
        from unittest import mock

        from core.forms import CadastroForm

        get_user_model().objects.create_user('existente', email='joao@exemplo.com', password='x')
        # Simula a corrida: a validação do formulário não vê o e-mail já gravado.
        with mock.patch.object(CadastroForm, 'clean_email', lambda self: self.cleaned_data['email'].lower()):
            resp = self.client.post(reverse('cadastro'), CadastroTests.dados)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'acabou de ser cadastrado')
        self.assertFalse(get_user_model().objects.filter(username='joao').exists())

    def test_admin_recusa_email_repetido_ao_editar(self):
        User = get_user_model()
        User.objects.create_user('dono', email='dono@exemplo.com', password='x')
        outro = User.objects.create_user('outro', email='outro@exemplo.com', password='x')
        chefe = User.objects.create_superuser('chefe', email='chefe@exemplo.com', password='x')
        self.client.force_login(chefe)
        url = reverse('admin:auth_user_change', args=[outro.pk])
        resp = self.client.post(url, {
            'username': 'outro', 'email': 'DONO@exemplo.com', 'is_active': 'on',
            'date_joined_0': '01/01/2026', 'date_joined_1': '00:00:00',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Já existe um usuário cadastrado com este e-mail.')
        outro.refresh_from_db()
        self.assertEqual(outro.email, 'outro@exemplo.com')
