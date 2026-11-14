"""Cliente do MNI 2.2.2 (namespace http://www.cnj.jus.br/servico-intercomunicacao-2.2.2/).

Usado por PJe, Projudi, e-SAJ, eproc (srv=intercomunicacao2.2) e serviços próprios
(ex.: TJRJ). Diferenças em relação ao MNI 3.0 que moldam esta classe:

- A autenticação vai em idConsultante/senhaConsultante, direto na requisição.
- A resposta traz sucesso/mensagem soltos, sem recibo.
- Os dados do processo vêm quase todos em atributos (numero, classeProcessual, nomeOrgao...).
- Não existe consultarDocumentosProcesso: o conteúdo de um documento é pedido ao próprio
  consultarProcesso, informando o identificador em <documento>.
- Não existe consultarCompetencias: a competência fica só com o código.
- Datas no formato AAAAMMDDHHMMSS, sem fuso: são horários de Brasília.
"""

from xml.etree.ElementTree import Element, SubElement, tostring

from django.utils import timezone

from .base import ClienteMNI
from .comum import (
    Aviso, ConteudoDocumento, Documento, Movimento, Parte, Polo, Processo, Representante, ResultadoAvisos,
    ResultadoDocumentos, ResultadoProcesso, conteudo_binario, corpo_resposta, data, filho, filhos, numero,
    ordem_data, organizar_processo, separar_mtom, texto, textos,
)

NS_SOAP = 'http://schemas.xmlsoap.org/soap/envelope/'
NS_SERVICO = 'http://www.cnj.jus.br/servico-intercomunicacao-2.2.2/'
NS_TIPOS_SERVICO = 'http://www.cnj.jus.br/tipos-servico-intercomunicacao-2.2.2'


class ClienteMNI222(ClienteMNI):
    VERSAO = '2.2.2'
    NAMESPACE_WSDL = NS_SERVICO
    PREFIXO_ACAO = NS_SERVICO
    OPERACAO_DOCUMENTO = 'consultarProcesso'

    # --- envelopes ---------------------------------------------------------------------------

    @staticmethod
    def envelope_avisos_pendentes(login, senha):
        env, _ = _requisicao('consultarAvisosPendentes', login, senha)
        return tostring(env, encoding='utf-8', xml_declaration=True)

    @staticmethod
    def envelope_consultar_processo(login, senha, numero, incluir_documentos=True):
        # A ordem dos elementos segue a <sequence> de tipoConsultarProcesso.
        return _envelope_processo(login, senha, numero, movimentos=True, cabecalho=True,
                                  documentos=incluir_documentos)

    @staticmethod
    def envelope_consultar_documento(login, senha, numero, id_documento):
        return _envelope_processo(login, senha, numero, movimentos=False, cabecalho=False,
                                  documentos=True, id_documento=id_documento)

    # --- interpretação -------------------------------------------------------------------------

    @staticmethod
    def interpretar_avisos_pendentes(conteudo):
        resposta = corpo_resposta(conteudo)
        sucesso, mensagens = _situacao(resposta)
        avisos = []
        for a in filhos(resposta, 'aviso'):
            processo = filho(a, 'processo')
            orgao = filho(processo, 'orgaoJulgador')
            avisos.append(Aviso(
                id=a.get('idAviso', ''),
                tipo_codigo=a.get('tipoComunicacao', ''),
                numero_processo=processo.get('numero', '') if processo is not None else '',
                destinatario=_nome_parte(filho(a, 'destinatario')),
                orgao_julgador=orgao.get('nomeOrgao', '') if orgao is not None else '',
                data_disponibilizacao=_data(texto(a, 'dataDisponibilizacao')),
                xml=tostring(a, encoding='unicode'),
            ))
        avisos.sort(key=lambda av: ordem_data(av.data_disponibilizacao), reverse=True)
        return ResultadoAvisos(sucesso=sucesso, mensagens=mensagens, avisos=avisos)

    @staticmethod
    def interpretar_processo(conteudo):
        resposta = corpo_resposta(conteudo)
        sucesso, mensagens = _situacao(resposta)
        el = filho(resposta, 'processo')
        if el is None:
            return ResultadoProcesso(sucesso=sucesso, mensagens=mensagens)

        basicos = filho(el, 'dadosBasicos')
        processo = Processo(numero=basicos.get('numero', '') if basicos is not None else '')
        if basicos is not None:
            _cabecalho(processo, basicos)
        for m in filhos(el, 'movimento'):
            complementos = textos(m, 'complemento') + textos(filho(m, 'movimentoNacional'), 'complemento')
            descricao = _descricao_movimento(m, complementos)
            processo.movimentos.append(Movimento(
                data_hora=_data(m.get('dataHora', '')),
                descricao=descricao,
                complementos=[c for c in complementos if c != descricao],
                id=m.get('identificadorMovimento', ''),
                ids_documentos=textos(m, 'idDocumentoVinculado'),
            ))
        organizar_processo(processo, [_documento(d) for d in filhos(el, 'documento')])
        return ResultadoProcesso(sucesso=sucesso, mensagens=mensagens, processo=processo)

    @staticmethod
    def interpretar_documentos(conteudo, content_type=''):
        """Documentos com conteúdo dentro de processo > documento (inclusive os vinculados).

        O conteúdo pode vir em base64 no XML ou como anexo MTOM (xop:Include).
        """
        conteudo, anexos = separar_mtom(conteudo, content_type)
        resposta = corpo_resposta(conteudo)
        sucesso, mensagens = _situacao(resposta)
        documentos = []
        pendentes = list(filhos(filho(resposta, 'processo'), 'documento'))
        while pendentes:
            d = pendentes.pop(0)
            pendentes.extend(filhos(d, 'documentoVinculado'))
            dados = conteudo_binario(filho(d, 'conteudo'), anexos)
            if dados:
                documentos.append(ConteudoDocumento(
                    id=d.get('idDocumento', ''), mimetype=d.get('mimetype') or 'application/octet-stream', dados=dados,
                ))
        return ResultadoDocumentos(sucesso=sucesso, mensagens=mensagens, documentos=documentos)


# --- auxiliares do MNI 2.2.2 ---------------------------------------------------------------------

def _requisicao(nome, login, senha):
    env = Element(f'{{{NS_SOAP}}}Envelope')
    corpo = SubElement(env, f'{{{NS_SOAP}}}Body')
    req = SubElement(corpo, f'{{{NS_SERVICO}}}{nome}')
    SubElement(req, f'{{{NS_TIPOS_SERVICO}}}idConsultante').text = login
    SubElement(req, f'{{{NS_TIPOS_SERVICO}}}senhaConsultante').text = senha
    return env, req


def _envelope_processo(login, senha, numero, *, movimentos, cabecalho, documentos, id_documento=None):
    env, req = _requisicao('consultarProcesso', login, senha)
    SubElement(req, f'{{{NS_TIPOS_SERVICO}}}numeroProcesso').text = numero
    for nome, valor in (('movimentos', movimentos), ('incluirCabecalho', cabecalho), ('incluirDocumentos', documentos)):
        SubElement(req, f'{{{NS_TIPOS_SERVICO}}}{nome}').text = 'true' if valor else 'false'
    if id_documento is not None:
        SubElement(req, f'{{{NS_TIPOS_SERVICO}}}documento').text = id_documento
    return tostring(env, encoding='utf-8', xml_declaration=True)


def _situacao(resposta):
    sucesso = texto(resposta, 'sucesso').lower() == 'true'
    mensagem = texto(resposta, 'mensagem')
    return sucesso, [mensagem] if mensagem else []


def _data(valor):
    """AAAAMMDDHHMMSS sem fuso é horário de Brasília (fuso do projeto)."""
    convertida = data(valor)
    if convertida is not None and timezone.is_naive(convertida):
        convertida = timezone.make_aware(convertida, timezone.get_default_timezone())
    return convertida


def _nome_parte(parte):
    if parte is None:
        return ''
    pessoa = filho(parte, 'pessoa')
    if pessoa is not None:
        return pessoa.get('nome', '')
    return texto(parte, 'interessePublico')


def _cabecalho(processo, basicos):
    processo.classe = basicos.get('classeProcessual', '')
    processo.competencia = basicos.get('competencia', '')
    processo.codigo_localidade = basicos.get('codigoLocalidade', '')
    processo.data_ajuizamento = _data(basicos.get('dataAjuizamento', ''))
    processo.nivel_sigilo = numero(basicos.get('nivelSigilo', ''), int)
    orgao = filho(basicos, 'orgaoJulgador')
    if orgao is not None:
        processo.orgao_julgador = orgao.get('nomeOrgao', '')
    processo.valor_causa = numero(texto(basicos, 'valorCausa'))
    processo.outros_numeros = textos(basicos, 'outrosnumeros')
    processo.prioridades = textos(basicos, 'prioridade')
    processo.parametros = [(o.get('nome', ''), o.get('valor', '')) for o in filhos(basicos, 'outroParametro') if o.get('nome')]
    for a in filhos(basicos, 'assunto'):
        assunto_local = filho(a, 'assuntoLocal')
        if assunto_local is not None and assunto_local.get('descricao'):
            processo.assuntos.append(assunto_local.get('descricao'))
        elif texto(a, 'codigoNacional'):
            processo.assuntos.append(f"Assunto {texto(a, 'codigoNacional')}")
    for p in filhos(basicos, 'polo'):
        polo = Polo(codigo=p.get('polo', ''))
        for parte in filhos(p, 'parte'):
            pessoa = filho(parte, 'pessoa')
            polo.partes.append(Parte(
                nome=_nome_parte(parte) or '—',
                documento=pessoa.get('numeroDocumentoPrincipal', '') if pessoa is not None else '',
                tipo_pessoa=pessoa.get('tipoPessoa', '') if pessoa is not None else '',
                representantes=[
                    Representante(nome=a.get('nome', ''), inscricao=a.get('inscricao', ''),
                                  tipo=a.get('tipoRepresentante', ''))
                    for a in filhos(parte, 'advogado') if a.get('nome')
                ],
            ))
        processo.polos.append(polo)


def _descricao_movimento(m, complementos):
    movimento_local = filho(m, 'movimentoLocal')
    if movimento_local is not None and movimento_local.get('descricao'):
        return movimento_local.get('descricao')
    if complementos:
        return complementos[0]
    nacional = filho(m, 'movimentoNacional')
    if nacional is not None:
        return f"Movimento {nacional.get('codigoNacional', '')}".strip()
    return 'Movimento'


def _documento(el) -> Documento:
    # O conteúdo (base64) é ignorado de propósito: só os dados do documento são usados.
    params = {p.get('nome'): p.get('valor', '') for p in filhos(el, 'outroParametro')}
    return Documento(
        id=el.get('idDocumento', ''),
        descricao=el.get('descricao', ''),
        rotulo=params.get('rotulo', ''),
        data_hora=_data(el.get('dataHora', '')),
        id_movimento=el.get('movimento', ''),
        nivel_sigilo=numero(el.get('nivelSigilo', ''), int),
        mimetype=el.get('mimetype', ''),
        tamanho=numero(params.get('tamanho', ''), int),
        vinculados=[_documento(v) for v in filhos(el, 'documentoVinculado')],
    )
