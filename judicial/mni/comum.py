"""Partes do cliente MNI que não dependem da versão do protocolo.

Aqui ficam só os dados já interpretados (processo, avisos, documentos), as exceções,
as formatações e a leitura genérica de SOAP. Nada aqui conhece elementos ou
namespaces de uma versão do MNI: isso fica na classe de cada versão.
"""

import base64
import binascii
import email
import email.policy
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import unquote

from defusedxml import ElementTree

TIPOS_COMUNICACAO = {
    'CIT': 'Citação',
    'INT': 'Intimação',
    'IDP': 'Intimação de despacho',
    'IDC': 'Intimação de decisão',
    'IAD': 'Intimação de audiência',
    'IST': 'Intimação de sentença',
    'IAC': 'Intimação de acórdão',
    'NOT': 'Notificação',
    'VIS': 'Vista para manifestação',
    'URG': 'Urgente',
    'PTA': 'Pauta de julgamento/audiência',
}
POLOS = {
    'AT': 'Polo ativo', 'PA': 'Polo passivo', 'TC': 'Terceiro', 'FL': 'Fiscal da lei',
    'TJ': 'Testemunha do juízo', 'AD': 'Amicus curiae', 'VI': 'Vítima',
}
TIPOS_PESSOA = {
    'FIS': 'Pessoa Física', 'JUR': 'Pessoa Jurídica', 'AUT': 'Autoridade',
    'ORP': 'Órgão de representação', 'EDP': 'Ente despersonalizado',
    'fisica': 'Pessoa Física', 'juridica': 'Pessoa Jurídica', 'autoridade': 'Autoridade',
    'orgaorepresentacao': 'Órgão de representação',
}
TIPOS_REPRESENTANTE = {
    'A': 'Advogado(a)', 'E': 'Escritório de advocacia', 'M': 'Ministério Público',
    'D': 'Defensoria Pública', 'P': 'Advocacia pública',
}
# outroParametro do eproc já exibidos em campos próprios (não repetir em "Informações adicionais").
PARAMETROS_USADOS = {
    'desClasse', 'desAssunto', 'situacao', 'dataAutuacao', 'orgaoJulgador', 'prioridade',
    'assuntoPrincipal', 'rito',  # 'rito' é o código; 'ritoProcessual' traz a descrição
}
NOMES_PARAMETROS = {
    'ritoProcessual': 'Rito processual',
    'tipoInquerito': 'Tipo de inquérito',
    'antecipacaoTutela': 'Antecipação de tutela',
}
PRIORIDADES = {'IDOSO': 'Idoso', 'REU_PRESO': 'Réu preso', 'PERECIMENTO': 'Perecimento', 'MENOR': 'Menor de idade'}
# Mensagens do tribunal que indicam usuário/senha recusados.
FALHA_AUTENTICACAO = re.compile(r'autentica|senha|usu[aá]rio.*inv[aá]lid', re.IGNORECASE)
UNIDADES_PRAZO = {'HOR': ('hora', 'horas'), 'DIA': ('dia', 'dias'), 'MES': ('mês', 'meses'), 'ANO': ('ano', 'anos')}
NS_XOP = 'http://www.w3.org/2004/08/xop/include'


class ErroMNI(Exception):
    """Falha de comunicação com o serviço. A mensagem pode ser exibida ao usuário."""


class RespostaGrandeDemais(ErroMNI):
    """A resposta passou do tamanho máximo (ex.: tribunal enviou o conteúdo dos documentos)."""


class NumeroInvalido(ValueError):
    pass


# --- formatações --------------------------------------------------------------------------

def normalizar_numero_processo(texto: str) -> str:
    """Aceita o número com ou sem máscara e devolve os 20 dígitos, validando o dígito verificador (CNJ)."""
    digitos = re.sub(r'\D', '', texto or '')
    if len(digitos) != 20:
        raise NumeroInvalido('O número do processo deve ter 20 dígitos (padrão CNJ: NNNNNNN-DD.AAAA.J.TR.OOOO).')
    sequencial, dv, resto = digitos[:7], digitos[7:9], digitos[9:]
    if int(dv) != 98 - int(sequencial + resto + '00') % 97:
        raise NumeroInvalido('Número de processo inválido: o dígito verificador não confere.')
    return digitos


def formatar_documento(doc: str) -> str:
    """CPF (11 dígitos) ou CNPJ (14 dígitos) com máscara; outros valores sem alteração."""
    d = re.sub(r'\D', '', doc or '')
    if len(d) == 11:
        return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}'
    if len(d) == 14:
        return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'
    return doc or ''


def formatar_oab(inscricao: str) -> str:
    """TO0001234A -> OAB/TO 1234-A (padrão do tipoCadastroOAB)."""
    m = re.fullmatch(r'([A-Za-z]{2})0*(\d+)([A-Za-z])', inscricao or '')
    if not m:
        return inscricao or ''
    uf, numero, sufixo = m.groups()
    return f'OAB/{uf.upper()} {numero}' + ('' if sufixo.upper() == 'N' else f'-{sufixo.upper()}')


def formatar_numero_processo(n: str) -> str:
    """NNNNNNN-DD.AAAA.J.TR.OOOO (padrão CNJ)."""
    if len(n) == 20 and n.isdigit():
        return f'{n[:7]}-{n[7:9]}.{n[9:13]}.{n[13]}.{n[14:16]}.{n[16:]}'
    return n


def rotulo(nome):
    """tipoInquerito -> 'Tipo inquerito' (rótulo genérico para parâmetros desconhecidos)."""
    palavras = re.sub(r'(?<!^)(?=[A-Z])', ' ', nome).split()
    return ' '.join([palavras[0].capitalize(), *(p.lower() for p in palavras[1:])]) if palavras else nome


# --- dados interpretados --------------------------------------------------------------------

class DescricaoAviso:
    """Descrições de um aviso (usadas pelo resultado da consulta e pelo aviso guardado no banco).

    Requer os atributos: tipo_codigo, numero_processo, tipo_prazo, prazo.
    """

    @property
    def tipo(self):
        return TIPOS_COMUNICACAO.get(self.tipo_codigo, self.tipo_codigo)

    @property
    def numero_processo_formatado(self):
        return formatar_numero_processo(self.numero_processo)

    @property
    def prazo_descricao(self):
        if self.tipo_prazo == 'SEMPRAZO':
            return 'Sem prazo'
        if self.tipo_prazo == 'DATA_CERTA':
            return 'Data certa'
        if self.tipo_prazo in UNIDADES_PRAZO and self.prazo is not None:
            singular, plural = UNIDADES_PRAZO[self.tipo_prazo]
            return f'{self.prazo} {singular if self.prazo == 1 else plural}'
        return '—'


@dataclass
class Aviso(DescricaoAviso):
    id: str
    tipo_codigo: str
    numero_processo: str
    destinatario: str = ''
    orgao_julgador: str = ''
    data_disponibilizacao: datetime | None = None
    tipo_prazo: str = ''
    prazo: int | None = None
    xml: str = ''  # elemento original do aviso, com todos os campos enviados pelo tribunal


@dataclass
class ResultadoAvisos:
    sucesso: bool
    mensagens: list[str] = field(default_factory=list)
    avisos: list[Aviso] = field(default_factory=list)


@dataclass
class Representante:
    nome: str
    inscricao: str = ''
    tipo: str = ''

    @property
    def tipo_descricao(self):
        return TIPOS_REPRESENTANTE.get(self.tipo, '')

    @property
    def inscricao_formatada(self):
        return formatar_oab(self.inscricao)


@dataclass
class Parte:
    nome: str
    documento: str = ''
    tipo_pessoa: str = ''
    representantes: list[Representante] = field(default_factory=list)

    @property
    def documento_formatado(self):
        return formatar_documento(self.documento)

    @property
    def tipo_pessoa_descricao(self):
        return TIPOS_PESSOA.get(self.tipo_pessoa, '')

    @property
    def advogados(self):
        return [r.nome for r in self.representantes]


@dataclass
class Polo:
    codigo: str
    partes: list[Parte] = field(default_factory=list)
    descricao: str = ''  # nome dado pelo tribunal (ex.: AUTOR, INVESTIGADO)

    @property
    def nome(self):
        return self.descricao or POLOS.get(self.codigo, self.codigo or 'Outros')


@dataclass
class Documento:
    id: str
    descricao: str = ''  # ex.: DECISÃO/DESPACHO
    rotulo: str = ''  # nome curto do eproc (outroParametro "rotulo"), ex.: DECDESPA1
    data_hora: datetime | None = None
    id_movimento: str = ''
    nivel_sigilo: int | None = None
    mimetype: str = ''
    tamanho: int | None = None  # bytes
    vinculados: list['Documento'] = field(default_factory=list)

    @property
    def nome(self):
        return self.rotulo or self.descricao or f'Documento {self.id}'

    @property
    def tamanho_formatado(self):
        if not self.tamanho:
            return ''
        return f'{self.tamanho / 1024:.0f} KB' if self.tamanho < 1024 * 1024 else f'{self.tamanho / 1024 / 1024:.1f} MB'


@dataclass
class Movimento:
    data_hora: datetime | None
    descricao: str
    complementos: list[str] = field(default_factory=list)
    numero: int = 0  # número do evento (1 = mais antigo)
    id: str = ''
    ids_documentos: list[str] = field(default_factory=list)
    documentos: list[Documento] = field(default_factory=list)

    @property
    def total_documentos(self):
        return len(self.documentos) or len(self.ids_documentos)


@dataclass
class Processo:
    numero: str
    classe: str = ''
    competencia: str = ''
    competencia_descricao: str = ''
    codigo_localidade: str = ''
    classe_descricao: str = ''
    situacao: str = ''
    prioridades_tribunal: list[str] = field(default_factory=list)
    orgao_julgador: str = ''
    data_ajuizamento: datetime | None = None
    valor_causa: float | None = None
    nivel_sigilo: int | None = None
    outros_numeros: list[str] = field(default_factory=list)
    prioridades: list[str] = field(default_factory=list)
    assuntos: list[str] = field(default_factory=list)
    polos: list[Polo] = field(default_factory=list)
    movimentos: list[Movimento] = field(default_factory=list)
    parametros: list[tuple[str, str]] = field(default_factory=list)

    @property
    def polos_principais(self):
        """Polo ativo e passivo, exibidos lado a lado."""
        return [p for p in self.polos if p.codigo in ('AT', 'PA')]

    @property
    def outros_polos(self):
        return [p for p in self.polos if p.codigo not in ('AT', 'PA')]

    @property
    def prioridades_descricao(self):
        if self.prioridades_tribunal:
            return self.prioridades_tribunal
        return [PRIORIDADES.get(p, p) for p in self.prioridades]

    @property
    def informacoes_adicionais(self):
        """Parâmetros extras do tribunal ainda não exibidos em outro lugar, com rótulos legíveis."""
        return [
            (NOMES_PARAMETROS.get(nome) or rotulo(nome), valor)
            for nome, valor in self.parametros
            if nome not in PARAMETROS_USADOS and not nome.startswith('desTipoParte')
        ]

    @property
    def numero_formatado(self):
        return formatar_numero_processo(self.numero)


@dataclass
class ResultadoProcesso:
    sucesso: bool
    mensagens: list[str] = field(default_factory=list)
    processo: Processo | None = None
    documentos_omitidos: bool = False  # resposta grande demais: consultado de novo sem documentos


@dataclass
class ConteudoDocumento:
    id: str
    mimetype: str
    dados: bytes
    encoding: str = ''


@dataclass
class ResultadoDocumentos:
    sucesso: bool
    mensagens: list[str] = field(default_factory=list)
    documentos: list[ConteudoDocumento] = field(default_factory=list)


def falha_de_autenticacao(resultado) -> bool:
    return not resultado.sucesso and any(FALHA_AUTENTICACAO.search(m) for m in resultado.mensagens)


def organizar_processo(processo: Processo, documentos: list[Documento]):
    """Associa os documentos aos eventos e numera os eventos (1 = mais antigo), do mais recente ao mais antigo.

    Cada documento vai para o seu evento pelo id do movimento indicado no documento ou
    pelos ids de documentos indicados no evento (o MNI admite as duas formas).
    """
    por_id = {d.id: d for d in documentos if d.id}
    por_movimento = {}
    for d in documentos:
        if d.id_movimento:
            por_movimento.setdefault(d.id_movimento, []).append(d)
    for mov in processo.movimentos:
        docs = list(por_movimento.get(mov.id, [])) if mov.id else []
        for id_doc in mov.ids_documentos:
            if id_doc in por_id and por_id[id_doc] not in docs:
                docs.append(por_id[id_doc])
        mov.documentos = docs
    processo.movimentos.sort(key=lambda mv: ordem_data(mv.data_hora))
    for i, mv in enumerate(processo.movimentos, start=1):
        mv.numero = i
    processo.movimentos.sort(key=lambda mv: ordem_data(mv.data_hora), reverse=True)


# --- leitura de XML/SOAP (independente da versão) ---------------------------------------------

def local(tag):
    return tag.rsplit('}', 1)[-1]


def filho(el, nome):
    if el is None:
        return None
    for f in el:
        if local(f.tag) == nome:
            return f
    return None


def filhos(el, nome):
    return [f for f in el if local(f.tag) == nome] if el is not None else []


def texto(el, *caminho):
    for nome in caminho:
        if el is None:
            return ''
        el = filho(el, nome)
    return (el.text or '').strip() if el is not None else ''


def textos(el, nome):
    """Textos não vazios dos filhos com esse nome."""
    return [(f.text or '').strip() for f in filhos(el, nome) if (f.text or '').strip()]


def data(valor):
    if not valor:
        return None
    for formato in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S', '%Y%m%d%H%M%S'):
        try:
            return datetime.strptime(valor.replace('Z', '+00:00'), formato)
        except ValueError:
            continue
    return None


def ordem_data(valor):
    """Chave de ordenação que aceita datas vazias (vão para o fim) e com/sem fuso horário."""
    return valor.timestamp() if valor is not None else float('-inf')


def numero(valor, tipo=float):
    try:
        return tipo(valor) if valor else None
    except ValueError:
        return None


def corpo_resposta(conteudo):
    """Primeiro elemento do Body do envelope SOAP; SOAP Fault vira ErroMNI."""
    try:
        raiz = ElementTree.fromstring(conteudo)
    except Exception as exc:
        raise ErroMNI('O tribunal enviou uma resposta inválida.') from exc
    corpo = filho(raiz, 'Body')
    if corpo is None or not len(corpo):
        raise ErroMNI('O tribunal enviou uma resposta inválida.')
    resposta = corpo[0]
    if local(resposta.tag) == 'Fault':
        detalhe = texto(resposta, 'faultstring') or 'erro não especificado'
        raise ErroMNI(f'O tribunal recusou a consulta: {detalhe}')
    return resposta


def separar_mtom(conteudo, content_type):
    """Resposta MTOM/XOP (multipart/related): devolve (XML raiz, {content-id: bytes}).

    Sem multipart, devolve o próprio conteúdo e nenhum anexo.
    """
    if 'multipart/related' not in (content_type or '').lower():
        return conteudo, {}
    mensagem = email.message_from_bytes(
        f'Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n'.encode() + conteudo,
        policy=email.policy.compat32,
    )
    partes = [p for p in mensagem.walk() if not p.is_multipart()]
    if not partes:
        raise ErroMNI('O tribunal enviou uma resposta inválida.')
    anexos = {}
    for parte in partes[1:]:
        cid = (parte.get('Content-ID') or '').strip().strip('<>')
        anexos[cid] = parte.get_payload(decode=True) or b''
    return partes[0].get_payload(decode=True) or b'', anexos


def conteudo_binario(el, anexos) -> bytes:
    """Bytes de um elemento base64Binary: texto em base64 ou referência MTOM (xop:Include)."""
    if el is None:
        return b''
    include = next((f for f in el if f.tag == f'{{{NS_XOP}}}Include'), None)
    if include is not None:
        cid = unquote(include.get('href', '').removeprefix('cid:'))
        if cid not in anexos:
            raise ErroMNI('O tribunal enviou o documento em um formato inesperado.')
        return anexos[cid]
    if not (el.text or '').strip():
        return b''
    try:
        return base64.b64decode(''.join(el.text.split()), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ErroMNI('O conteúdo do documento recebido do tribunal está corrompido.') from exc
