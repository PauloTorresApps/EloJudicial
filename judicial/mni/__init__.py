"""Cliente do MNI (Modelo Nacional de Interoperabilidade - CNJ).

Cada versão do protocolo tem a sua classe, sem código compartilhado entre versões:

- v300.ClienteMNI300: MNI 3.0
- v222.ClienteMNI222: MNI 2.2.2

A versão de cada ambiente é reconhecida pelo WSDL (versoes.cliente). As funções abaixo
são a interface usada pelo restante do sistema e não dependem da versão.
"""

from .base import ClienteMNI
from .comum import (
    Aviso, ConteudoDocumento, DescricaoAviso, Documento, ErroMNI, Movimento, NumeroInvalido, Parte, Polo,
    Processo, Representante, RespostaGrandeDemais, ResultadoAvisos, ResultadoDocumentos, ResultadoProcesso,
    falha_de_autenticacao, formatar_documento, formatar_numero_processo, formatar_oab, normalizar_numero_processo,
)
from .v222 import ClienteMNI222
from .v300 import ClienteMNI300
from .versoes import cliente


def consultar_avisos_pendentes(ambiente, login, senha, ao_receber=None) -> ResultadoAvisos:
    """O tribunal entrega cada aviso uma única vez: veja ClienteMNI.consultar_avisos_pendentes."""
    return cliente(ambiente).consultar_avisos_pendentes(login, senha, ao_receber=ao_receber)


def consultar_processo(ambiente, login, senha, numero) -> ResultadoProcesso:
    return cliente(ambiente).consultar_processo(login, senha, numero)


def consultar_documento(ambiente, login, senha, numero, id_documento) -> ResultadoDocumentos:
    return cliente(ambiente).consultar_documento(login, senha, numero, id_documento)


def descrever_competencia(ambiente, processo):
    """Preenche processo.competencia_descricao quando a versão permite. Falhas não impedem a exibição."""
    try:
        servico = cliente(ambiente)
    except ErroMNI:
        return
    servico.descrever_competencia(processo)


__all__ = [
    'Aviso', 'ClienteMNI', 'ClienteMNI222', 'ClienteMNI300', 'ConteudoDocumento', 'DescricaoAviso', 'Documento',
    'ErroMNI', 'Movimento', 'NumeroInvalido', 'Parte', 'Polo', 'Processo', 'Representante', 'RespostaGrandeDemais',
    'ResultadoAvisos', 'ResultadoDocumentos', 'ResultadoProcesso', 'cliente', 'consultar_avisos_pendentes',
    'consultar_documento', 'consultar_processo', 'descrever_competencia', 'falha_de_autenticacao',
    'formatar_documento', 'formatar_numero_processo', 'formatar_oab', 'normalizar_numero_processo',
]
