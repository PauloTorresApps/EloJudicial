"""Senha do usuário nos sistemas judiciais — nunca persistida.

A senha digitada é convertida imediatamente em sha256(senha.encode('utf-8')).hexdigest();
somente esse hash é guardado, e apenas no cache (Redis em memória), vinculado à sessão
de login do usuário e ao sistema. Ele expira após settings.MNI_SENHA_TTL, é apagado
ao sair do Jilo e quando o tribunal recusar a autenticação.

Atenção: para o MNI esse hash equivale à senha. Nunca registre-o em log nem o exiba.
"""

from hashlib import sha256

from django.conf import settings
from django.contrib.auth.signals import user_logged_out
from django.core.cache import cache
from django.dispatch import receiver


def gerar_hash(senha: str) -> str:
    return sha256(senha.encode('utf-8')).hexdigest()


def _chave(request, sistema_id):
    if not request.session.session_key:
        request.session.save()
    return f'mni:senha:{request.user.pk}:{request.session.session_key}:{sistema_id}'


def _chave_indice(request):
    return f'mni:senha:{request.user.pk}:{request.session.session_key}:indice'


def guardar(request, sistema, senha: str):
    """Converte a senha em hash e guarda só o hash no cache. A senha original é descartada."""
    chave = _chave(request, sistema.pk)
    cache.set(chave, gerar_hash(senha), settings.MNI_SENHA_TTL)
    indice = cache.get(_chave_indice(request), set()) | {sistema.pk}
    cache.set(_chave_indice(request), indice, settings.MNI_SENHA_TTL)


def obter(request, sistema):
    """Hash da senha do sistema, ou None se precisar ser informada (de novo)."""
    if not request.user.is_authenticated or not request.session.session_key:
        return None
    return cache.get(_chave(request, sistema.pk))


def sistemas_desbloqueados(request):
    if not request.user.is_authenticated or not request.session.session_key:
        return set()
    indice = cache.get(_chave_indice(request), set())
    chaves = {_chave(request, pk): pk for pk in indice}
    return {chaves[c] for c in cache.get_many(list(chaves))}


def esquecer(request, sistema):
    cache.delete(_chave(request, sistema.pk))


def esquecer_todas(request):
    if not request.session.session_key:
        return
    indice = cache.get(_chave_indice(request), set())
    cache.delete_many([_chave(request, pk) for pk in indice] + [_chave_indice(request)])


@receiver(user_logged_out)
def _ao_sair(sender, request, user, **kwargs):
    # Disparado antes de a sessão ser apagada, então as chaves ainda podem ser montadas.
    if request is not None and user is not None:
        request.user = user
        esquecer_todas(request)
