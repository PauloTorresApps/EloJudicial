"""Única saída HTTP do EloJudicial para os tribunais (SOAP do MNI e download do WSDL).

Toda requisição passa pelo limitador (judicial/limitador.py): uma por vez por tribunal, com
intervalo mínimo, porque rajadas de requisições podem levar ao bloqueio do IP. Nenhum outro
módulo deve chamar urllib.request.urlopen (há um teste que verifica isso).
"""

import urllib.error
import urllib.request
from dataclasses import dataclass

from .limitador import vez_do_tribunal

USER_AGENT = 'EloJudicial/1.0 (+integracao MNI)'


@dataclass
class Resposta:
    conteudo: bytes  # até limite + 1 bytes: quem chama compara com o limite
    tipo: str = ''  # Content-Type
    status: int = 200


def requisitar(url, *, timeout, limite, dados=None, cabecalhos=None) -> Resposta:
    """GET (ou POST, com `dados`) na vez do tribunal, lendo no máximo limite + 1 bytes.

    Erro HTTP volta como Resposta com o status (o SOAP Fault vem com HTTP 500 e corpo XML).
    As demais falhas são propagadas: limitador.TribunalOcupado, TimeoutError, URLError, OSError.
    """
    requisicao = urllib.request.Request(url, data=dados, headers={'User-Agent': USER_AGENT, **(cabecalhos or {})})
    with vez_do_tribunal(url):
        try:
            with urllib.request.urlopen(requisicao, timeout=timeout) as resposta:
                return Resposta(resposta.read(limite + 1), _tipo(resposta), getattr(resposta, 'status', 200))
        except urllib.error.HTTPError as exc:
            return Resposta(exc.read(limite + 1), _tipo(exc), exc.code)


def _tipo(resposta):
    cabecalhos = getattr(resposta, 'headers', None)
    return cabecalhos.get('Content-Type', '') if cabecalhos else ''
