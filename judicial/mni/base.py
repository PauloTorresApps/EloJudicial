"""Classe base dos clientes MNI: transporte HTTP/SOAP e o roteiro de cada consulta.

Cada versão do MNI é uma subclasse própria (v300.ClienteMNI300, v222.ClienteMNI222) que
monta os envelopes e interpreta as respostas daquela versão. A base não conhece nenhum
elemento ou namespace do protocolo.

A senha do consultante nunca é registrada em log nem incluída em mensagens de erro.
"""

import logging
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

from .comum import ErroMNI, ResultadoAvisos, ResultadoDocumentos, ResultadoProcesso, RespostaGrandeDemais

logger = logging.getLogger('judicial.mni')

TIMEOUT = 60  # segundos
TAMANHO_MAXIMO = 20 * 1024 * 1024
TAMANHO_MAXIMO_DOCUMENTO = 100 * 1024 * 1024


class ClienteMNI(ABC):
    """Cliente de um ambiente (serviço MNI de um tribunal)."""

    VERSAO = ''  # ex.: '3.0'
    NAMESPACE_WSDL = ''  # targetNamespace do WSDL que identifica a versão
    PREFIXO_ACAO = ''  # SOAPAction = PREFIXO_ACAO + nome da operação
    OPERACAO_AVISOS = 'consultarAvisosPendentes'
    OPERACAO_PROCESSO = 'consultarProcesso'
    OPERACAO_DOCUMENTO = ''  # operação usada para obter o conteúdo de um documento

    def __init__(self, ambiente, endereco):
        self.ambiente = ambiente
        self.endereco = endereco

    # --- montagem e interpretação: cada versão implementa as suas --------------------------

    @staticmethod
    @abstractmethod
    def envelope_avisos_pendentes(login, senha) -> bytes: ...

    @staticmethod
    @abstractmethod
    def interpretar_avisos_pendentes(conteudo) -> ResultadoAvisos: ...

    @staticmethod
    @abstractmethod
    def envelope_consultar_processo(login, senha, numero, incluir_documentos=True) -> bytes: ...

    @staticmethod
    @abstractmethod
    def interpretar_processo(conteudo) -> ResultadoProcesso: ...

    @staticmethod
    @abstractmethod
    def envelope_consultar_documento(login, senha, numero, id_documento) -> bytes: ...

    @staticmethod
    @abstractmethod
    def interpretar_documentos(conteudo, content_type='') -> ResultadoDocumentos: ...

    # --- consultas ---------------------------------------------------------------------------

    def consultar_avisos_pendentes(self, login, senha, ao_receber=None) -> ResultadoAvisos:
        """ATENÇÃO: o tribunal entrega cada aviso pendente uma única vez — depois desta consulta
        ele não volta mais. Por isso ao_receber(conteudo) recebe a resposta bruta ANTES de ela
        ser interpretada, para que seja guardada mesmo que a interpretação falhe.
        """
        conteudo = self._enviar(self.OPERACAO_AVISOS, self.envelope_avisos_pendentes(login, senha))
        if ao_receber is not None:
            ao_receber(conteudo)
        return self.interpretar_avisos_pendentes(conteudo)

    def consultar_processo(self, login, senha, numero) -> ResultadoProcesso:
        try:
            conteudo = self._enviar(self.OPERACAO_PROCESSO, self.envelope_consultar_processo(login, senha, numero))
        except RespostaGrandeDemais:
            # Processo muito grande com documentos: consulta de novo sem eles para não deixar o usuário sem resposta.
            logger.warning('consultarProcesso %s: resposta grande demais com documentos; repetindo sem documentos', numero)
            corpo = self.envelope_consultar_processo(login, senha, numero, incluir_documentos=False)
            resultado = self.interpretar_processo(self._enviar(self.OPERACAO_PROCESSO, corpo))
            resultado.documentos_omitidos = True
            return resultado
        return self.interpretar_processo(conteudo)

    def consultar_documento(self, login, senha, numero, id_documento) -> ResultadoDocumentos:
        """Conteúdo de um documento do processo."""
        corpo = self.envelope_consultar_documento(login, senha, numero, id_documento)
        conteudo, tipo = self._enviar_bruto(self.OPERACAO_DOCUMENTO, corpo, limite=TAMANHO_MAXIMO_DOCUMENTO)
        # Só metadados (nunca o conteúdo): ajuda a diagnosticar o formato usado pelo tribunal.
        logger.info('%s (MNI %s): %d bytes, Content-Type %s',
                    self.OPERACAO_DOCUMENTO, self.VERSAO, len(conteudo), tipo or '(vazio)')
        return self.interpretar_documentos(conteudo, tipo)

    def descrever_competencia(self, processo):
        """Preenche processo.competencia_descricao, se a versão oferecer a tabela de competências."""

    # --- transporte --------------------------------------------------------------------------

    def _enviar(self, operacao, corpo):
        return self._enviar_bruto(operacao, corpo)[0]

    def _enviar_bruto(self, operacao, corpo, limite=TAMANHO_MAXIMO):
        """Envia a requisição SOAP e devolve (corpo da resposta, Content-Type)."""
        requisicao = urllib.request.Request(
            self.endereco, data=corpo, method='POST',
            headers={'Content-Type': 'text/xml; charset=utf-8', 'SOAPAction': f'"{self.PREFIXO_ACAO}{operacao}"',
                     'User-Agent': 'EloJudicial/1.0 (+integracao MNI)'},
        )
        tipo = ''
        try:
            with urllib.request.urlopen(requisicao, timeout=TIMEOUT) as resposta:
                conteudo = resposta.read(limite + 1)
                tipo = resposta.headers.get('Content-Type', '') if getattr(resposta, 'headers', None) else ''
        except urllib.error.HTTPError as exc:
            # SOAP Fault costuma vir com HTTP 500 e corpo XML.
            conteudo = exc.read(limite + 1)
            tipo = exc.headers.get('Content-Type', '') if exc.headers else ''
            if not conteudo:
                logger.warning('MNI %s: HTTP %s sem corpo', operacao, exc.code)
                raise ErroMNI(f'O tribunal respondeu com erro (HTTP {exc.code}). Tente novamente mais tarde.') from exc
        except TimeoutError as exc:
            raise ErroMNI('O tribunal demorou demais para responder. Tente novamente em instantes.') from exc
        except (urllib.error.URLError, OSError) as exc:
            logger.warning('MNI %s: falha de conexão: %s', operacao, exc)
            raise ErroMNI('Não foi possível conectar ao tribunal. Tente novamente em instantes.') from exc
        if len(conteudo) > limite:
            raise RespostaGrandeDemais('A resposta do tribunal excedeu o tamanho máximo permitido.')
        return conteudo, tipo
