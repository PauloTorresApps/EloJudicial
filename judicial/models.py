from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


class SistemaJudicial(models.Model):
    nome = models.CharField('nome', max_length=100)
    descricao = models.CharField('descrição', max_length=255, blank=True)
    url = models.URLField('portal (URL)', blank=True, help_text='Endereço do portal web do sistema, para acesso pelo navegador.')
    ativo = models.BooleanField(
        'ativo', default=True,
        help_text='Somente sistemas ativos aparecem para os usuários cadastrarem credenciais.',
    )
    criado_em = models.DateTimeField('criado em', auto_now_add=True)
    atualizado_em = models.DateTimeField('atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'sistema judicial'
        verbose_name_plural = 'sistemas judiciais'
        ordering = ['nome']
        constraints = [
            models.UniqueConstraint(
                Lower('nome'), name='judicial_sistema_nome_unico',
                violation_error_message='Já existe um sistema judicial com este nome.',
            ),
        ]

    def __str__(self):
        return self.nome


class Ambiente(models.Model):
    """Ambiente de trabalho de um sistema judicial (ex.: 1º Grau, 2º Grau).

    Cada ambiente aponta para a URL do serviço de integração (WSDL do MNI) que
    será usada pelo EloJudicial quando o usuário trabalhar nesse ambiente.
    """

    sistema = models.ForeignKey(
        SistemaJudicial, on_delete=models.CASCADE, related_name='ambientes', verbose_name='sistema judicial',
    )
    descricao = models.CharField('descrição', max_length=100, help_text='Ex.: 1º Grau, 2º Grau, Turmas Recursais.')
    url_servico = models.URLField(
        'URL do serviço de integração', max_length=500,
        help_text='Endereço do WSDL do serviço (ex.: MNI 3.0).',
    )
    ordem = models.PositiveSmallIntegerField('ordem', default=0, help_text='Ordem de exibição (menor primeiro).')
    ativo = models.BooleanField('ativo', default=True)
    criado_em = models.DateTimeField('criado em', auto_now_add=True)
    atualizado_em = models.DateTimeField('atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'ambiente'
        verbose_name_plural = 'ambientes'
        ordering = ['sistema__nome', 'ordem', 'descricao']
        constraints = [
            models.UniqueConstraint(
                models.F('sistema'), Lower('descricao'), name='judicial_ambiente_descricao_unica',
                violation_error_message='Este sistema já tem um ambiente com esta descrição.',
            ),
            models.UniqueConstraint(
                fields=['sistema', 'url_servico'], name='judicial_ambiente_url_unica',
                violation_error_message='Este sistema já tem um ambiente com esta URL.',
            ),
        ]

    def __str__(self):
        return f'{self.sistema} — {self.descricao}'


class Credencial(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='credenciais_judiciais', verbose_name='usuário do EloJudicial',
    )
    sistema = models.ForeignKey(
        SistemaJudicial, on_delete=models.PROTECT,
        related_name='credenciais', verbose_name='sistema judicial',
    )
    login = models.CharField(
        'usuário no sistema', max_length=150,
        help_text='Login usado para acessar o sistema judicial (ex.: CPF ou matrícula).',
    )
    criado_em = models.DateTimeField('criado em', auto_now_add=True)
    atualizado_em = models.DateTimeField('atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'credencial'
        verbose_name_plural = 'credenciais'
        ordering = ['sistema__nome']
        constraints = [
            models.UniqueConstraint(
                fields=['usuario', 'sistema'], name='judicial_credencial_usuario_sistema_unico',
                violation_error_message='Você já tem uma credencial cadastrada para este sistema.',
            ),
        ]

    def __str__(self):
        # Sem o login: o texto aparece em telas da administração (ex.: ao excluir um usuário).
        return f'Credencial do {self.sistema}'


class Preferencia(models.Model):
    """Preferências do usuário na área judicial (ex.: ambiente de trabalho padrão)."""

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='preferencia_judicial', verbose_name='usuário',
    )
    ambiente_padrao = models.ForeignKey(
        Ambiente, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        verbose_name='ambiente padrão',
        help_text='Ambiente aberto automaticamente quando o usuário entra no EloJudicial.',
    )
    atualizado_em = models.DateTimeField('atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'preferência'
        verbose_name_plural = 'preferências'

    def __str__(self):
        return f'Preferências de {self.usuario}'
