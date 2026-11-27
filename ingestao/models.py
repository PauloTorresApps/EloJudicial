"""Lotes de documentos ingeridos dos tribunais, para análise posterior (agentes de IA).

Cada lote nasce dos toggles marcados na tabela de eventos de um processo e pertence só ao
usuário que o criou: os documentos são baixados com as credenciais dele no tribunal.
"""

from django.conf import settings
from django.core.files.storage import storages
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver

from judicial.mni import formatar_numero_processo

from . import extracao


def _armazenamento():
    return storages['ingestao']


def _caminho(arquivo, nome):
    return f'{arquivo.lote.usuario_id}/{arquivo.lote_id}/{nome}'


class LoteIngestao(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='lotes_ingestao', verbose_name='usuário',
    )
    ambiente = models.ForeignKey('judicial.Ambiente', on_delete=models.PROTECT, related_name='+', verbose_name='ambiente')
    numero_processo = models.CharField('número do processo', max_length=20)
    login = models.CharField('usuário no sistema', max_length=150)
    criado_em = models.DateTimeField('criado em', auto_now_add=True)

    class Meta:
        verbose_name = 'lote de ingestão'
        verbose_name_plural = 'lotes de ingestão'
        ordering = ['-criado_em']

    def __str__(self):
        # Sem dados do processo: o texto pode aparecer em telas da administração.
        return f'Lote de ingestão #{self.pk}'

    @property
    def numero_formatado(self):
        return formatar_numero_processo(self.numero_processo)


class ArquivoIngerido(models.Model):
    class Situacao(models.TextChoices):
        PENDENTE = 'pendente', 'Na fila'
        BAIXANDO = 'baixando', 'Baixando'
        PRONTO = 'pronto', 'Pronto'
        PARCIAL = 'parcial', 'Texto parcial'  # parte das páginas precisa de OCR
        SEM_TEXTO = 'sem_texto', 'Sem texto'
        AGUARDANDO_SENHA = 'aguardando_senha', 'Aguardando senha'
        ERRO = 'erro', 'Erro'

    EM_ANDAMENTO = (Situacao.PENDENTE, Situacao.BAIXANDO)
    CONCLUIDOS = (Situacao.PRONTO, Situacao.PARCIAL, Situacao.SEM_TEXTO)
    RETOMAVEIS = (Situacao.AGUARDANDO_SENHA, Situacao.ERRO)
    COM_TEXTO = (Situacao.PRONTO, Situacao.PARCIAL)

    lote = models.ForeignKey(LoteIngestao, on_delete=models.CASCADE, related_name='arquivos', verbose_name='lote')
    id_documento = models.CharField('identificador do documento', max_length=100)
    nome = models.CharField('nome', max_length=255, blank=True)
    descricao = models.CharField('descrição', max_length=255, blank=True)
    tipo_local = models.CharField('tipo de documento (código local)', max_length=20, blank=True)
    evento = models.PositiveIntegerField('evento', null=True, blank=True, help_text='Número do evento no processo (1 = mais antigo).')
    situacao = models.CharField('situação', max_length=20, choices=Situacao, default=Situacao.PENDENTE, db_index=True)
    mensagem = models.TextField('mensagem', blank=True)
    arquivo = models.FileField('arquivo original', storage=_armazenamento, upload_to=_caminho, max_length=255, blank=True)
    mimetype = models.CharField('tipo do arquivo', max_length=100, blank=True)
    tamanho = models.PositiveBigIntegerField('tamanho (bytes)', null=True, blank=True)
    sha256 = models.CharField('SHA-256', max_length=64, blank=True)
    codificacao = models.CharField('codificação informada pelo tribunal', max_length=50, blank=True)
    atualizado_em = models.DateTimeField('atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'arquivo ingerido'
        verbose_name_plural = 'arquivos ingeridos'
        ordering = ['pk']
        constraints = [
            models.UniqueConstraint(fields=['lote', 'id_documento'], name='ingestao_arquivo_unico_por_lote'),
        ]

    def __str__(self):
        return f'Arquivo ingerido #{self.pk}'

    @property
    def rotulo(self):
        return self.nome or self.descricao or f'Documento {self.id_documento}'

    @property
    def variante(self):
        """Cor da etiqueta da situação (unfold/helpers/label.html)."""
        return {
            self.Situacao.PRONTO: 'success', self.Situacao.PARCIAL: 'warning', self.Situacao.SEM_TEXTO: 'warning',
            self.Situacao.AGUARDANDO_SENHA: 'warning', self.Situacao.ERRO: 'danger',
        }.get(self.situacao, 'info')

    @property
    def tem_texto(self):
        return self.situacao in self.COM_TEXTO

    @property
    def texto(self):
        """Texto extraído do arquivo inteiro: o das páginas, em ordem, separadas por uma linha em branco."""
        return '\n\n'.join(t for t in self.paginas.values_list('texto', flat=True) if t)


class PaginaExtraida(models.Model):
    """Uma página do arquivo (PDF), ou a única página de um HTML, texto ou imagem.

    Guarda o texto e as métricas usadas para decidir se a página precisa de OCR (ver
    ingestao/extracao.py e docs/0013). As métricas ficam para calibrar os limiares.
    """

    class Situacao(models.TextChoices):  # valores definidos pela classificação (extracao.py)
        COM_TEXTO = extracao.COM_TEXTO, 'Com texto'
        EM_BRANCO = extracao.EM_BRANCO, 'Em branco'
        SEM_TEXTO = extracao.SEM_TEXTO, 'Digitalizada (sem texto)'
        TEXTO_RUIM = extracao.TEXTO_RUIM, 'Texto de baixa qualidade'

    PRECISAM_OCR = extracao.PRECISAM_OCR

    class Metodo(models.TextChoices):
        NATIVO = 'nativo', 'Texto do próprio arquivo'

    arquivo = models.ForeignKey(ArquivoIngerido, on_delete=models.CASCADE, related_name='paginas', verbose_name='arquivo')
    numero = models.PositiveIntegerField('página')
    situacao = models.CharField('situação', max_length=20, choices=Situacao, db_index=True)
    metodo = models.CharField('método de extração', max_length=20, choices=Metodo, default=Metodo.NATIVO)
    texto = models.TextField('texto', blank=True)
    caracteres = models.PositiveIntegerField('caracteres (sem espaços)', default=0)
    qualidade = models.FloatField('qualidade do texto (0 a 1)', null=True, blank=True)
    maior_imagem = models.PositiveBigIntegerField('maior imagem (pixels)', default=0)

    class Meta:
        verbose_name = 'página extraída'
        verbose_name_plural = 'páginas extraídas'
        ordering = ['arquivo', 'numero']
        constraints = [
            models.UniqueConstraint(fields=['arquivo', 'numero'], name='ingestao_pagina_unica_por_arquivo'),
        ]

    def __str__(self):
        return f'Página {self.numero} do arquivo #{self.arquivo_id}'


@receiver(post_delete, sender=ArquivoIngerido)
def _apagar_original(sender, instance, **kwargs):
    # O registro saiu do banco: o arquivo no volume também (inclusive ao apagar o lote ou o usuário).
    if instance.arquivo:
        instance.arquivo.delete(save=False)
