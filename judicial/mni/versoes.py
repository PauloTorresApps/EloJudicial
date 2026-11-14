"""Escolha do cliente MNI de um ambiente, pela versão que o próprio WSDL declara."""

import logging

from ..wsdl import ErroWSDL, descrever_servico
from .base import ClienteMNI
from .comum import ErroMNI
from .v222 import ClienteMNI222
from .v300 import ClienteMNI300

logger = logging.getLogger('judicial.mni')

# Uma classe por versão; a chave é o targetNamespace do WSDL.
CLIENTES: dict[str, type[ClienteMNI]] = {cls.NAMESPACE_WSDL: cls for cls in (ClienteMNI300, ClienteMNI222)}


def cliente(ambiente) -> ClienteMNI:
    """Cliente da versão do MNI publicada no WSDL do ambiente, já com o endereço de execução."""
    try:
        servico = descrever_servico(ambiente.url_servico)
    except ErroWSDL as exc:
        logger.warning('WSDL indisponível para o ambiente %s: %s', ambiente.pk, exc)
        raise ErroMNI('O serviço do tribunal está indisponível no momento. Tente novamente em instantes.') from exc
    classe = CLIENTES.get(servico.namespace)
    if classe is None:
        logger.warning('Ambiente %s: versão do MNI não suportada (namespace %r)', ambiente.pk, servico.namespace)
        raise ErroMNI('A versão do MNI deste serviço ainda não é suportada pelo EloJudicial. Avise o administrador.')
    if not servico.endereco:
        raise ErroMNI('O serviço deste ambiente não informou o endereço de execução. Avise o administrador.')
    return classe(ambiente, servico.endereco)
