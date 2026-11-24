"""Acesso ao tribunal enquanto um lote é processado em segundo plano.

O worker não tem a sessão do usuário. Ao criar (ou retomar) um lote, o hash da senha do
sistema é copiado para uma chave do lote no cache: o Redis SEM persistência, o mesmo das
senhas (judicial/senhas.py). Nunca vai para a fila do Celery, que é persistida em disco.
A chave expira junto com a senha (settings.MNI_SENHA_TTL), é apagada quando o tribunal recusa
a senha e quando o usuário sai do EloJudicial.
"""

from django.conf import settings
from django.contrib.auth.signals import user_logged_out
from django.core.cache import cache
from django.dispatch import receiver


def _chave(lote_id):
    return f'mni:ingestao:{lote_id}'


def guardar(lote, senha_hash):
    cache.set(_chave(lote.pk), senha_hash, settings.MNI_SENHA_TTL)


def obter(lote):
    return cache.get(_chave(lote.pk))


def esquecer(lote):
    cache.delete(_chave(lote.pk))


@receiver(user_logged_out)
def _ao_sair(sender, request, user, **kwargs):
    if user is None:
        return
    from .models import ArquivoIngerido, LoteIngestao

    lotes = LoteIngestao.objects.filter(
        usuario=user, arquivos__situacao__in=ArquivoIngerido.EM_ANDAMENTO,
    ).values_list('pk', flat=True).distinct()
    cache.delete_many([_chave(pk) for pk in lotes])
