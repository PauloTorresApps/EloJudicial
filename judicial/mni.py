"""Cliente do MNI 3.0 (Modelo Nacional de Interoperabilidade - CNJ).

Operações implementadas: consultarAvisosPendentes e consultarProcesso.
A senha do consultante nunca é registrada em log nem incluída em mensagens de erro.
"""

import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from xml.etree.ElementTree import Element, SubElement, tostring

from defusedxml import ElementTree
from django.core.cache import cache

from .wsdl import ErroWSDL, descrever_servico

logger = logging.getLogger(__name__)

NS_SOAP = 'http://schemas.xmlsoap.org/soap/envelope/'
NS_MNI = 'http://www.cnj.jus.br/mni/v300/'
NS_SERVICO = 'http://www.cnj.jus.br/mni/v300/tipos-servico-intercomunicacao'
NS_TIPOS = 'http://www.cnj.jus.br/mni/v300/intercomunicacao'
TIMEOUT = 60  # segundos
TAMANHO_MAXIMO = 20 * 1024 * 1024
TAMANHO_MAXIMO_DOCUMENTO = 100 * 1024 * 1024
CACHE_TABELAS = 24 * 60 * 60  # tabelas de apoio (ex.: competências) mudam raramente

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


class ErroMNI(Exception):
    """Falha de comunicação com o serviço. A mensagem pode ser exibida ao usuário."""


class RespostaGrandeDemais(ErroMNI):
    """A resposta passou de TAMANHO_MAXIMO (ex.: tribunal enviou o conteúdo dos documentos)."""


class NumeroInvalido(ValueError):
    pass


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


@dataclass
class Aviso:
    id: str
    tipo_codigo: str
    numero_processo: str
    destinatario: str = ''
    orgao_julgador: str = ''
    data_disponibilizacao: datetime | None = None
    tipo_prazo: str = ''
    prazo: int | None = None

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
            (NOMES_PARAMETROS.get(nome) or _rotulo(nome), valor)
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


# --- montagem da requisição ---------------------------------------------------------

def _requisicao(nome, login, senha):
    env = Element(f'{{{NS_SOAP}}}Envelope')
    corpo = SubElement(env, f'{{{NS_SOAP}}}Body')
    req = SubElement(corpo, f'{{{NS_MNI}}}{nome}')
    consultante = SubElement(req, f'{{{NS_SERVICO}}}consultante')
    auth = SubElement(consultante, f'{{{NS_TIPOS}}}autenticacaoSimples')
    SubElement(auth, f'{{{NS_TIPOS}}}usuario').text = login
    SubElement(auth, f'{{{NS_TIPOS}}}senha').text = senha
    return env, req


def _envelope_avisos_pendentes(login, senha):
    env, _ = _requisicao('requisicaoConsultarAvisosPendentes', login, senha)
    return tostring(env, encoding='utf-8', xml_declaration=True)


def _envelope_consultar_processo(login, senha, numero, incluir_documentos=True):
    env, req = _requisicao('requisicaoConsultarProcesso', login, senha)
    # A ordem dos elementos segue a <sequence> do XSD.
    SubElement(req, f'{{{NS_SERVICO}}}numeroProcesso').text = numero
    for nome, valor in (('incluirCabecalho', 'true'), ('incluirPartes', 'true'), ('incluirEnderecos', 'false'),
                        ('incluirMovimentos', 'true'), ('incluirDocumentos', 'true' if incluir_documentos else 'false')):
        SubElement(req, f'{{{NS_SERVICO}}}{nome}').text = valor
    # Parâmetros adicionais (último elemento da sequência): pede as descrições
    # de classe, assuntos, movimentos etc., além dos códigos.
    SubElement(req, f'{{{NS_SERVICO}}}parametros', {'nome': 'incluirDescricoes', 'valor': 'true'})
    return tostring(env, encoding='utf-8', xml_declaration=True)


def _envelope_consultar_documentos(login, senha, numero, ids_documentos):
    env, req = _requisicao('requisicaoConsultarDocumentosProcesso', login, senha)
    SubElement(req, f'{{{NS_SERVICO}}}numeroProcesso').text = numero
    for id_documento in ids_documentos:
        SubElement(req, f'{{{NS_SERVICO}}}idDocumento').text = id_documento
    return tostring(env, encoding='utf-8', xml_declaration=True)


def _envelope_consultar_competencias(codigo_localidade):
    # Operação de suporte: não exige autenticação do consultante.
    env = Element(f'{{{NS_SOAP}}}Envelope')
    corpo = SubElement(env, f'{{{NS_SOAP}}}Body')
    req = SubElement(corpo, f'{{{NS_MNI}}}requisicaoConsultarCompetencias')
    SubElement(req, f'{{{NS_SERVICO}}}codigoLocalidade').text = codigo_localidade
    return tostring(env, encoding='utf-8', xml_declaration=True)


def _enviar(endereco, acao, corpo):
    return _enviar_bruto(endereco, acao, corpo)[0]


def _enviar_bruto(endereco, acao, corpo, limite=TAMANHO_MAXIMO):
    """Envia a requisição SOAP e devolve (corpo da resposta, Content-Type)."""
    requisicao = urllib.request.Request(
        endereco, data=corpo, method='POST',
        headers={'Content-Type': 'text/xml; charset=utf-8', 'SOAPAction': f'"{NS_MNI}{acao}"',
                 'User-Agent': 'Jilo/1.0 (+integracao MNI)'},
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
            logger.warning('MNI %s: HTTP %s sem corpo', acao, exc.code)
            raise ErroMNI(f'O tribunal respondeu com erro (HTTP {exc.code}). Tente novamente mais tarde.') from exc
    except TimeoutError as exc:
        raise ErroMNI('O tribunal demorou demais para responder. Tente novamente em instantes.') from exc
    except (urllib.error.URLError, OSError) as exc:
        logger.warning('MNI %s: falha de conexão: %s', acao, exc)
        raise ErroMNI('Não foi possível conectar ao tribunal. Tente novamente em instantes.') from exc
    if len(conteudo) > limite:
        raise RespostaGrandeDemais('A resposta do tribunal excedeu o tamanho máximo permitido.')
    return conteudo, tipo


# --- interpretação da resposta ---------------------------------------------------------

def _local(tag):
    return tag.rsplit('}', 1)[-1]


def _filho(el, nome):
    for f in el:
        if _local(f.tag) == nome:
            return f
    return None


def _filhos(el, nome):
    return [f for f in el if _local(f.tag) == nome]


def _texto(el, *caminho):
    for nome in caminho:
        if el is None:
            return ''
        el = _filho(el, nome)
    return (el.text or '').strip() if el is not None else ''


def _data(valor):
    if not valor:
        return None
    for formato in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S', '%Y%m%d%H%M%S'):
        try:
            return datetime.strptime(valor.replace('Z', '+00:00'), formato)
        except ValueError:
            continue
    return None


def _ordem_data(valor):
    """Chave de ordenação que aceita datas vazias (vão para o fim) e com/sem fuso horário."""
    return valor.timestamp() if valor is not None else float('-inf')


def _corpo_resposta(conteudo):
    try:
        raiz = ElementTree.fromstring(conteudo)
    except Exception as exc:
        raise ErroMNI('O tribunal enviou uma resposta inválida.') from exc
    corpo = _filho(raiz, 'Body')
    if corpo is None or not len(corpo):
        raise ErroMNI('O tribunal enviou uma resposta inválida.')
    resposta = corpo[0]
    if _local(resposta.tag) == 'Fault':
        detalhe = _texto(resposta, 'faultstring') or 'erro não especificado'
        raise ErroMNI(f'O tribunal recusou a consulta: {detalhe}')
    return resposta


def _recibo(resposta):
    recibo = _filho(resposta, 'recibo')
    sucesso = _texto(recibo, 'sucesso').lower() == 'true'
    mensagens = [
        _texto(m, 'descritivo').lstrip('->').strip()
        for m in (_filhos(recibo, 'mensagens') if recibo is not None else [])
    ]
    return sucesso, [m for m in mensagens if m]


def interpretar_avisos_pendentes(conteudo) -> ResultadoAvisos:
    resposta = _corpo_resposta(conteudo)
    sucesso, mensagens = _recibo(resposta)
    avisos = []
    for a in _filhos(resposta, 'avisos'):
        prazo = _texto(a, 'prazo')
        avisos.append(Aviso(
            id=_texto(a, 'idAviso'),
            tipo_codigo=_texto(a, 'tipoComunicacao'),
            numero_processo=_texto(a, 'numeroProcesso') or _texto(a, 'processo', 'numero'),
            destinatario=_texto(a, 'destinatario', 'nome'),
            orgao_julgador=_texto(a, 'remetente', 'nome') or _texto(a, 'processo', 'orgaoJulgador', 'nome'),
            data_disponibilizacao=_data(_texto(a, 'dataDisponibilizacao')),
            tipo_prazo=_texto(a, 'tipoPrazo'),
            prazo=int(prazo) if prazo.isdigit() else None,
        ))
    avisos.sort(key=lambda av: _ordem_data(av.data_disponibilizacao), reverse=True)
    return ResultadoAvisos(sucesso=sucesso, mensagens=mensagens, avisos=avisos)


def _campo(el, nome):
    """Valor de um campo que pode vir como elemento filho ou como atributo (variações entre tribunais)."""
    return _texto(el, nome) or el.get(nome, '')


def _documento(el) -> Documento:
    # O conteúdo (base64) é ignorado de propósito: só os dados do documento são usados.
    params = {p.get('nome'): p.get('valor', '') for p in _filhos(el, 'outroParametro')}
    conteudo = _filho(el, 'conteudo')
    return Documento(
        id=_campo(el, 'idDocumento'),
        descricao=_campo(el, 'descricao'),
        rotulo=params.get('rotulo', ''),
        data_hora=_data(_campo(el, 'dataHora')),
        id_movimento=_campo(el, 'idMovimento'),
        nivel_sigilo=_numero(_campo(el, 'nivelSigilo'), int),
        mimetype=_texto(conteudo, 'mimetype') if conteudo is not None else '',
        tamanho=_numero(params.get('tamanho') or _campo(el, 'tamanhoConteudo'), int),
        vinculados=[_documento(v) for v in _filhos(el, 'documentoVinculado')],
    )


def _relacionar_documentos(processo, documentos):
    """Associa cada documento ao seu evento: pelo idMovimento do documento ou pelo
    idDocumentoVinculado do evento (o MNI admite as duas formas)."""
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


def _rotulo(nome):
    """tipoInquerito -> 'Tipo inquerito' (rótulo genérico para parâmetros desconhecidos)."""
    palavras = re.sub(r'(?<!^)(?=[A-Z])', ' ', nome).split()
    return ' '.join([palavras[0].capitalize(), *(p.lower() for p in palavras[1:])]) if palavras else nome


def _aplicar_parametros(processo):
    """Usa os outroParametro do eproc (com incluirDescricoes=true) para completar o processo."""
    params = {}
    for nome, valor in processo.parametros:
        params.setdefault(nome, valor)
    processo.classe_descricao = params.get('desClasse', '')
    processo.situacao = params.get('situacao', '')
    processo.prioridades_tribunal = [v for n, v in processo.parametros if n == 'prioridade' and v]
    if params.get('dataAutuacao'):
        processo.data_ajuizamento = _data(params['dataAutuacao']) or processo.data_ajuizamento
    if not processo.orgao_julgador:
        processo.orgao_julgador = params.get('orgaoJulgador', '')
    # Descrição do assunto principal (o MNI só traz o código).
    if params.get('desAssunto'):
        principal = params.get('assuntoPrincipal', '')
        rotulo = f'Assunto {principal}'
        if principal and rotulo in processo.assuntos:
            processo.assuntos[processo.assuntos.index(rotulo)] = params['desAssunto']
        elif params['desAssunto'] not in processo.assuntos:
            processo.assuntos.insert(0, params['desAssunto'])
    for polo in processo.polos:
        polo.descricao = params.get(f'desTipoParte{polo.codigo}', '')


def _numero(valor, tipo=float):
    try:
        return tipo(valor) if valor else None
    except ValueError:
        return None


def _descricao_movimento(m):
    local = _filho(m, 'movimentoLocal')
    if local is not None and local.get('descricao'):
        return local.get('descricao')
    nacional = _filho(m, 'movimentoNacional')
    complementos = [(c.text or '').strip() for c in _filhos(m, 'complemento') if (c.text or '').strip()]
    if complementos:
        return complementos[0]
    if nacional is not None:
        return f"Movimento {nacional.get('codigoNacional', '')}".strip()
    return 'Movimento'


def interpretar_processo(conteudo) -> ResultadoProcesso:
    resposta = _corpo_resposta(conteudo)
    sucesso, mensagens = _recibo(resposta)
    el = _filho(resposta, 'processo')
    if el is None:
        return ResultadoProcesso(sucesso=sucesso, mensagens=mensagens)

    basicos = _filho(el, 'dadosBasicos')
    # MNI 3.0: dadosBasicos (tipoCabecalhoProcesso) > dadosBasicos (tipoCabecalhoProcessoSimples) > numero
    numero = (
        _texto(basicos, 'dadosBasicos', 'numero') or _texto(basicos, 'numero') or _texto(el, 'numeroProcesso')
        or (basicos.get('numero', '') if basicos is not None else '')
    )
    processo = Processo(numero=numero)
    if basicos is not None:
        processo.classe = _texto(basicos, 'classeProcessual') or basicos.get('classeProcessual', '')
        processo.competencia = _texto(basicos, 'competencia')
        processo.codigo_localidade = _texto(basicos, 'codigoLocalidade')
        orgao = _filho(basicos, 'orgaoJulgador')
        if orgao is not None:
            processo.orgao_julgador = _texto(orgao, 'nome') or orgao.get('nomeOrgao', '')
        processo.data_ajuizamento = _data(_texto(basicos, 'dataAjuizamento') or basicos.get('dataAjuizamento', ''))
        processo.valor_causa = _numero(_texto(basicos, 'valorCausa'))
        processo.nivel_sigilo = _numero(_texto(basicos, 'nivelSigilo') or basicos.get('nivelSigilo', ''), int)
        processo.outros_numeros = [(o.text or '').strip() for o in _filhos(basicos, 'outrosNumeros') if (o.text or '').strip()]
        processo.prioridades = [(o.text or '').strip() for o in _filhos(basicos, 'prioridade') if (o.text or '').strip()]
        processo.parametros = [
            (o.get('nome', ''), o.get('valor', '') or (o.text or '').strip())
            for o in _filhos(basicos, 'outroParametro') if o.get('nome')
        ]
        for a in _filhos(basicos, 'assunto'):
            local = _filho(a, 'assuntoLocal')
            if local is not None and local.get('descricao'):
                processo.assuntos.append(local.get('descricao'))
            elif _texto(a, 'codigoNacional'):
                processo.assuntos.append(f"Assunto {_texto(a, 'codigoNacional')}")
        for p in _filhos(basicos, 'polo'):
            polo = Polo(codigo=p.get('polo', ''))
            for parte in _filhos(p, 'parte'):
                pessoa = _filho(parte, 'pessoa')
                dados = _filho(pessoa, 'dadosBasicos') if pessoa is not None else None
                origem = dados if dados is not None else pessoa
                polo.partes.append(Parte(
                    nome=_texto(origem, 'nome') or '—',
                    documento=_texto(origem, 'numeroDocumentoPrincipal'),
                    tipo_pessoa=_texto(origem, 'qualificacaoPessoa'),
                    representantes=[
                        Representante(nome=a.get('nome', ''), inscricao=a.get('inscricao', ''),
                                      tipo=a.get('tipoRepresentante', ''))
                        for a in _filhos(parte, 'advogado') if a.get('nome')
                    ],
                ))
            processo.polos.append(polo)

    for m in _filhos(el, 'movimento'):
        complementos = [(c.text or '').strip() for c in _filhos(m, 'complemento') if (c.text or '').strip()]
        descricao = _descricao_movimento(m)
        processo.movimentos.append(Movimento(
            data_hora=_data(m.get('dataHora', '')),
            descricao=descricao,
            complementos=[c for c in complementos if c != descricao],
            id=m.get('idMovimento', ''),
            ids_documentos=[(d.text or '').strip() for d in _filhos(m, 'idDocumentoVinculado') if (d.text or '').strip()],
        ))
    _relacionar_documentos(processo, [_documento(d) for d in _filhos(el, 'documento')])
    _aplicar_parametros(processo)
    # Numeração dos eventos em ordem cronológica (1 = mais antigo), como nos sistemas judiciais.
    processo.movimentos.sort(key=lambda mv: _ordem_data(mv.data_hora))
    for i, mv in enumerate(processo.movimentos, start=1):
        mv.numero = i
    processo.movimentos.sort(key=lambda mv: _ordem_data(mv.data_hora), reverse=True)
    return ResultadoProcesso(sucesso=sucesso, mensagens=mensagens, processo=processo)


def interpretar_competencias(conteudo) -> dict[str, str]:
    """{código: descrição} das competências da localidade."""
    resposta = _corpo_resposta(conteudo)
    sucesso, mensagens = _recibo(resposta)
    if not sucesso:
        raise ErroMNI('O tribunal não retornou as competências: ' + ' '.join(mensagens))
    return {
        _texto(c, 'codigo'): _texto(c, 'descricao')
        for c in _filhos(resposta, 'competencias') if _texto(c, 'codigo')
    }


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


NS_XOP = 'http://www.w3.org/2004/08/xop/include'


def _separar_mtom(conteudo, content_type):
    """Resposta MTOM/XOP (multipart/related): devolve (XML raiz, {content-id: bytes})."""
    import email
    import email.policy

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


def interpretar_documentos(conteudo, content_type='') -> ResultadoDocumentos:
    """Aceita o conteúdo em base64 no XML ou como anexo MTOM (xop:Include)."""
    import base64
    import binascii
    from urllib.parse import unquote

    anexos = {}
    if 'multipart/related' in (content_type or '').lower():
        conteudo, anexos = _separar_mtom(conteudo, content_type)
    resposta = _corpo_resposta(conteudo)
    sucesso, mensagens = _recibo(resposta)
    documentos = []
    for d in _filhos(resposta, 'documentos'):
        el = _filho(d, 'conteudo')
        dados = b''
        if el is not None:
            include = next((f for f in el if f.tag == f'{{{NS_XOP}}}Include'), None)
            if include is not None:
                cid = unquote(include.get('href', '').removeprefix('cid:'))
                if cid not in anexos:
                    raise ErroMNI('O tribunal enviou o documento em um formato inesperado.')
                dados = anexos[cid]
            elif (el.text or '').strip():
                try:
                    dados = base64.b64decode(''.join(el.text.split()), validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ErroMNI('O conteúdo do documento recebido do tribunal está corrompido.') from exc
        documentos.append(ConteudoDocumento(
            id=_texto(d, 'idDocumento'), mimetype=_texto(d, 'mimetype') or 'application/octet-stream',
            dados=dados, encoding=_texto(d, 'encoding'),
        ))
    return ResultadoDocumentos(sucesso=sucesso, mensagens=mensagens, documentos=documentos)


# --- operações -------------------------------------------------------------------------------

def endereco_servico(ambiente):
    """Endereço de execução (soap:address) obtido do WSDL do ambiente."""
    try:
        endereco = descrever_servico(ambiente.url_servico).endereco
    except ErroWSDL as exc:
        logger.warning('WSDL indisponível para o ambiente %s: %s', ambiente.pk, exc)
        raise ErroMNI('O serviço do tribunal está indisponível no momento. Tente novamente em instantes.') from exc
    if not endereco:
        raise ErroMNI('O serviço deste ambiente não informou o endereço de execução. Avise o administrador.')
    return endereco


def consultar_avisos_pendentes(ambiente, login, senha) -> ResultadoAvisos:
    corpo = _envelope_avisos_pendentes(login, senha)
    conteudo = _enviar(endereco_servico(ambiente), 'consultarAvisosPendentes', corpo)
    return interpretar_avisos_pendentes(conteudo)


def consultar_processo(ambiente, login, senha, numero) -> ResultadoProcesso:
    endereco = endereco_servico(ambiente)
    try:
        conteudo = _enviar(endereco, 'consultarProcesso', _envelope_consultar_processo(login, senha, numero))
    except RespostaGrandeDemais:
        # Processo muito grande com documentos: consulta de novo sem eles para não deixar o usuário sem resposta.
        logger.warning('consultarProcesso %s: resposta grande demais com documentos; repetindo sem documentos', numero)
        corpo = _envelope_consultar_processo(login, senha, numero, incluir_documentos=False)
        resultado = interpretar_processo(_enviar(endereco, 'consultarProcesso', corpo))
        resultado.documentos_omitidos = True
        return resultado
    return interpretar_processo(conteudo)


def falha_de_autenticacao(resultado) -> bool:
    return not resultado.sucesso and any(FALHA_AUTENTICACAO.search(m) for m in resultado.mensagens)


def consultar_competencias(ambiente, codigo_localidade) -> dict[str, str]:
    """Tabela de competências da localidade (MNI consultarCompetencias), com cache de 24 horas."""
    chave = f'mni:competencias:{ambiente.pk}:{codigo_localidade}'
    if (tabela := cache.get(chave)) is not None:
        return tabela
    corpo = _envelope_consultar_competencias(codigo_localidade)
    tabela = interpretar_competencias(_enviar(endereco_servico(ambiente), 'consultarCompetencias', corpo))
    if tabela:  # tabela vazia pode ser localidade de outra instância ou falha momentânea: não fixa em cache
        cache.set(chave, tabela, CACHE_TABELAS)
    return tabela


def descrever_competencia(ambiente, processo):
    """Preenche processo.competencia_descricao. Falhas não impedem a exibição do processo."""
    if not processo.competencia or not processo.codigo_localidade:
        return
    try:
        tabela = consultar_competencias(ambiente, processo.codigo_localidade)
    except ErroMNI as exc:
        logger.warning('Competências indisponíveis (ambiente %s, localidade %s): %s',
                       ambiente.pk, processo.codigo_localidade, exc)
        return
    processo.competencia_descricao = tabela.get(processo.competencia, '')


def consultar_documento(ambiente, login, senha, numero, id_documento) -> ResultadoDocumentos:
    """Conteúdo de um documento do processo (MNI consultarDocumentosProcesso)."""
    corpo = _envelope_consultar_documentos(login, senha, numero, [id_documento])
    conteudo, tipo = _enviar_bruto(endereco_servico(ambiente), 'consultarDocumentosProcesso', corpo,
                                   limite=TAMANHO_MAXIMO_DOCUMENTO)
    # Só metadados (nunca o conteúdo): ajuda a diagnosticar o formato usado pelo tribunal.
    logger.info('consultarDocumentosProcesso: %d bytes, Content-Type %s', len(conteudo), tipo or '(vazio)')
    return interpretar_documentos(conteudo, tipo)
