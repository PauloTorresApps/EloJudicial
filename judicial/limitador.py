"""Limitador das requisições aos tribunais: uma por vez por tribunal, com intervalo mínimo entre elas.

REGRA DO PROJETO: nunca disparar várias requisições ao MNI num intervalo curto (em paralelo ou
em sequência rápida) — o tribunal pode bloquear o IP do EloJudicial. Todo acesso HTTP a um
tribunal passa por vez_do_tribunal(url), dentro de judicial.http.requisitar.

A coordenação usa o cache compartilhado (Redis), então vale entre todos os processos: os do
servidor web e os do worker. O intervalo (MNI_INTERVALO, em segundos) conta do fim de uma
requisição ao início da próxima ao mesmo tribunal.

Não aninhe vez_do_tribunal para o mesmo tribunal: a trava não é reentrante.
"""

import contextvars
import logging
import secrets
from contextlib import contextmanager
from time import monotonic, sleep, time as agora
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger('judicial.mni')

TRAVA_TTL = 5 * 60  # segundos: se o processo morrer no meio da requisição, a trava expira sozinha
PASSO = 0.2  # segundos entre as tentativas de pegar a trava
ESPERA_TELA = 20  # telas: o gunicorn encerra a requisição em 30 s

_espera_maxima = contextvars.ContextVar('espera_maxima', default=ESPERA_TELA)


class TribunalOcupado(Exception):
    """A vez de falar com o tribunal não chegou dentro da espera máxima."""


def chave_tribunal(url):
    """Os hosts de um mesmo tribunal contam juntos (eproc1g-ws.tjto.jus.br e eproc2g.hmlg.tjto.jus.br → tjto.jus.br)."""
    host = (urlsplit(url).hostname or '').lower()
    return '.'.join(host.split('.')[-3:])


@contextmanager
def paciencia(segundos):
    """Espera máxima pela vez do tribunal dentro do bloco (ex.: tarefas em segundo plano podem esperar mais)."""
    marca = _espera_maxima.set(segundos)
    try:
        yield
    finally:
        _espera_maxima.reset(marca)


@contextmanager
def vez_do_tribunal(url):
    """Executa o bloco com exclusividade sobre o tribunal da URL, respeitando o intervalo mínimo."""
    tribunal = chave_tribunal(url)
    trava, ultima = f'mni:trava:{tribunal}', f'mni:ultima:{tribunal}'
    token = secrets.token_hex(8)
    espera_maxima = _espera_maxima.get()
    limite = monotonic() + espera_maxima
    while not cache.add(trava, token, TRAVA_TTL):
        if monotonic() >= limite:
            logger.warning('Tribunal %s ocupado: vez não chegou em %s s', tribunal, espera_maxima)
            raise TribunalOcupado('O tribunal está atendendo outras consultas do EloJudicial. Tente novamente em instantes.')
        sleep(PASSO)
    intervalo = settings.MNI_INTERVALO
    try:
        anterior = cache.get(ultima)
        if anterior is not None and (espera := anterior + intervalo - agora()) > 0:
            sleep(espera)
        yield
    finally:
        cache.set(ultima, agora(), max(intervalo, 1) * 10)
        if cache.get(trava) == token:  # não apaga a trava de outro processo se a nossa expirou
            cache.delete(trava)
