"""Leitura do WSDL de um serviço SOAP: versão (targetNamespace) e endereço de execução."""

import hashlib
import logging
import urllib.request
from dataclasses import dataclass

from defusedxml import ElementTree
from django.core.cache import cache

logger = logging.getLogger(__name__)

NS = {
    'wsdl': 'http://schemas.xmlsoap.org/wsdl/',
    'soap': 'http://schemas.xmlsoap.org/wsdl/soap/',
    'soap12': 'http://schemas.xmlsoap.org/wsdl/soap12/',
}
TIMEOUT = 15  # segundos
TAMANHO_MAXIMO = 5 * 1024 * 1024  # 5 MB
USER_AGENT = 'EloJudicial/1.0 (+integracao MNI)'
CACHE_SEGUNDOS = 60 * 60


class ErroWSDL(Exception):
    """Falha ao obter ou interpretar o WSDL."""


@dataclass
class DescricaoServico:
    endereco: str  # soap:address do serviço
    namespace: str = ''  # targetNamespace do WSDL: identifica a versão do MNI


def _baixar(url):
    requisicao = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(requisicao, timeout=TIMEOUT) as resposta:
            conteudo = resposta.read(TAMANHO_MAXIMO + 1)
    except Exception as exc:
        raise ErroWSDL(f'Não foi possível acessar o serviço: {exc}') from exc
    if len(conteudo) > TAMANHO_MAXIMO:
        raise ErroWSDL('O WSDL excede o tamanho máximo permitido.')
    return conteudo


def interpretar(conteudo: bytes) -> DescricaoServico:
    try:
        raiz = ElementTree.fromstring(conteudo)
    except Exception as exc:
        raise ErroWSDL('O conteúdo retornado não é um WSDL válido.') from exc
    if raiz.tag != f'{{{NS["wsdl"]}}}definitions':
        raise ErroWSDL('O conteúdo retornado não é um WSDL 1.1.')

    servico = raiz.find('wsdl:service', NS)
    endereco = ''
    if servico is not None:
        for caminho in ('wsdl:port/soap:address', 'wsdl:port/soap12:address'):
            endereco_el = servico.find(caminho, NS)
            # Atenção: Element sem filhos é "falso"; comparar sempre com None.
            if endereco_el is not None:
                endereco = endereco_el.get('location', '')
                break

    return DescricaoServico(endereco=endereco, namespace=raiz.get('targetNamespace', ''))


def descrever_servico(url: str, usar_cache: bool = True) -> DescricaoServico:
    # v3: DescricaoServico sem as operações (entradas antigas no cache não são mais legíveis).
    chave = 'wsdl:v3:' + hashlib.sha256(url.encode()).hexdigest()
    if usar_cache and (em_cache := cache.get(chave)) is not None:
        return em_cache
    descricao = interpretar(_baixar(url))
    cache.set(chave, descricao, CACHE_SEGUNDOS)
    return descricao
