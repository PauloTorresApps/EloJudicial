"""Leitura das operações oferecidas por um serviço SOAP a partir do seu WSDL."""

import hashlib
import logging
import re
import urllib.request
from dataclasses import dataclass, field

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
CACHE_SEGUNDOS = 60 * 60

# Ordem de exibição dos grupos.
GRUPOS = ['Processos', 'Comunicações processuais', 'Peticionamento', 'Remessas', 'Tabelas de apoio']

# Títulos e grupos em português para as operações conhecidas do MNI 3.0.
# Operações fora desta lista usam um título gerado a partir do nome técnico.
OPERACOES_MNI = {
    'consultarProcesso': ('Consultar processo', 'Processos'),
    'consultarDocumentosProcesso': ('Consultar documentos do processo', 'Processos'),
    'consultarAlteracao': ('Consultar alteração', 'Processos'),
    'consultarAvisosPendentes': ('Consultar avisos pendentes', 'Comunicações processuais'),
    'consultarTeorComunicacao': ('Consultar teor da comunicação', 'Comunicações processuais'),
    'responderComunicacao': ('Responder comunicação', 'Comunicações processuais'),
    'entregarPeticao': ('Entregar petição', 'Peticionamento'),
    'entregarPeticaoInicial': ('Entregar petição inicial', 'Peticionamento'),
    'remeterProcesso': ('Remeter processo', 'Remessas'),
    'consultarRemessas': ('Consultar remessas', 'Remessas'),
    'confirmarRecebimento': ('Confirmar recebimento', 'Remessas'),
    'consultarAssuntos': ('Consultar assuntos', 'Tabelas de apoio'),
    'consultarClasses': ('Consultar classes', 'Tabelas de apoio'),
    'consultarCompetencias': ('Consultar competências', 'Tabelas de apoio'),
    'consultarLocalidades': ('Consultar localidades', 'Tabelas de apoio'),
}
OUTROS = 'Outras operações'


class ErroWSDL(Exception):
    """Falha ao obter ou interpretar o WSDL."""


@dataclass
class Operacao:
    nome: str
    documentacao: str = ''

    @property
    def titulo(self):
        if self.nome in OPERACOES_MNI:
            return OPERACOES_MNI[self.nome][0]
        # fazerAlgumaCoisa -> 'Fazer alguma coisa'
        palavras = re.sub(r'(?<!^)(?=[A-Z])', ' ', self.nome).split()
        return ' '.join([palavras[0].capitalize(), *(p.lower() for p in palavras[1:])])

    @property
    def grupo(self):
        return OPERACOES_MNI.get(self.nome, (None, OUTROS))[1]

    @property
    def resumo(self):
        """Primeira frase da documentação do WSDL."""
        texto = ' '.join(self.documentacao.split())
        frase = re.split(r'(?<=\.)\s', texto, maxsplit=1)[0]
        return frase


@dataclass
class DescricaoServico:
    nome: str
    endereco: str
    operacoes: list[Operacao] = field(default_factory=list)

    @property
    def grupos(self):
        """[(grupo, [operações]), ...] na ordem em que os grupos aparecem."""
        resultado = {}
        for op in sorted(self.operacoes, key=lambda o: o.titulo):
            resultado.setdefault(op.grupo, []).append(op)
        return sorted(resultado.items(), key=lambda g: GRUPOS.index(g[0]) if g[0] in GRUPOS else len(GRUPOS))


def _baixar(url):
    requisicao = urllib.request.Request(url, headers={'User-Agent': 'Jilo/1.0 (+integracao MNI)'})
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

    operacoes = {}
    for op in raiz.findall('wsdl:portType/wsdl:operation', NS):
        nome = op.get('name')
        if nome and nome not in operacoes:
            doc = op.find('wsdl:documentation', NS)
            operacoes[nome] = Operacao(nome=nome, documentacao=(doc.text or '').strip() if doc is not None else '')

    servico = raiz.find('wsdl:service', NS)
    endereco = ''
    if servico is not None:
        for caminho in ('wsdl:port/soap:address', 'wsdl:port/soap12:address'):
            endereco_el = servico.find(caminho, NS)
            # Atenção: Element sem filhos é "falso"; comparar sempre com None.
            if endereco_el is not None:
                endereco = endereco_el.get('location', '')
                break

    return DescricaoServico(
        nome=(servico.get('name') if servico is not None else '') or raiz.get('name', ''),
        endereco=endereco,
        operacoes=list(operacoes.values()),
    )


def descrever_servico(url: str, usar_cache: bool = True) -> DescricaoServico:
    chave = 'wsdl:' + hashlib.sha256(url.encode()).hexdigest()
    if usar_cache and (em_cache := cache.get(chave)) is not None:
        return em_cache
    descricao = interpretar(_baixar(url))
    cache.set(chave, descricao, CACHE_SEGUNDOS)
    return descricao
