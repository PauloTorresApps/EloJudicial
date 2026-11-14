"""Cliente do MNI 3.0 (namespace http://www.cnj.jus.br/mni/v300/).

Operações: consultarAvisosPendentes, consultarProcesso, consultarDocumentosProcesso e
consultarCompetencias. Usado pelo eproc e pelo PJe da Justiça do Trabalho.
"""

import logging
from xml.etree.ElementTree import Element, SubElement, tostring

from django.core.cache import cache

from .base import ClienteMNI
from .comum import (
    Aviso, ConteudoDocumento, Documento, ErroMNI, Movimento, Parte, Polo, Processo, Representante,
    ResultadoAvisos, ResultadoDocumentos, ResultadoProcesso, conteudo_binario, corpo_resposta, data, filho,
    filhos, numero, ordem_data, organizar_processo, separar_mtom, texto, textos,
)

logger = logging.getLogger('judicial.mni')

NS_SOAP = 'http://schemas.xmlsoap.org/soap/envelope/'
NS_MNI = 'http://www.cnj.jus.br/mni/v300/'
NS_SERVICO = 'http://www.cnj.jus.br/mni/v300/tipos-servico-intercomunicacao'
NS_TIPOS = 'http://www.cnj.jus.br/mni/v300/intercomunicacao'
CACHE_TABELAS = 24 * 60 * 60  # tabelas de apoio (ex.: competências) mudam raramente


class ClienteMNI300(ClienteMNI):
    VERSAO = '3.0'
    NAMESPACE_WSDL = NS_MNI
    PREFIXO_ACAO = NS_MNI
    OPERACAO_DOCUMENTO = 'consultarDocumentosProcesso'

    # --- envelopes ---------------------------------------------------------------------------

    @staticmethod
    def envelope_avisos_pendentes(login, senha):
        env, _ = _requisicao('requisicaoConsultarAvisosPendentes', login, senha)
        return tostring(env, encoding='utf-8', xml_declaration=True)

    @staticmethod
    def envelope_consultar_processo(login, senha, numero, incluir_documentos=True):
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

    @staticmethod
    def envelope_consultar_documento(login, senha, numero, id_documento):
        env, req = _requisicao('requisicaoConsultarDocumentosProcesso', login, senha)
        SubElement(req, f'{{{NS_SERVICO}}}numeroProcesso').text = numero
        SubElement(req, f'{{{NS_SERVICO}}}idDocumento').text = id_documento
        return tostring(env, encoding='utf-8', xml_declaration=True)

    @staticmethod
    def envelope_consultar_competencias(codigo_localidade):
        # Operação de suporte: não exige autenticação do consultante.
        env = Element(f'{{{NS_SOAP}}}Envelope')
        corpo = SubElement(env, f'{{{NS_SOAP}}}Body')
        req = SubElement(corpo, f'{{{NS_MNI}}}requisicaoConsultarCompetencias')
        SubElement(req, f'{{{NS_SERVICO}}}codigoLocalidade').text = codigo_localidade
        return tostring(env, encoding='utf-8', xml_declaration=True)

    # --- interpretação -------------------------------------------------------------------------

    @staticmethod
    def interpretar_avisos_pendentes(conteudo):
        resposta = corpo_resposta(conteudo)
        sucesso, mensagens = _recibo(resposta)
        avisos = []
        for a in filhos(resposta, 'avisos'):
            prazo = texto(a, 'prazo')
            avisos.append(Aviso(
                id=texto(a, 'idAviso'),
                tipo_codigo=texto(a, 'tipoComunicacao'),
                numero_processo=texto(a, 'numeroProcesso') or texto(a, 'processo', 'numero'),
                destinatario=texto(a, 'destinatario', 'nome'),
                orgao_julgador=texto(a, 'remetente', 'nome') or texto(a, 'processo', 'orgaoJulgador', 'nome'),
                data_disponibilizacao=data(texto(a, 'dataDisponibilizacao')),
                tipo_prazo=texto(a, 'tipoPrazo'),
                prazo=int(prazo) if prazo.isdigit() else None,
                xml=tostring(a, encoding='unicode'),
            ))
        avisos.sort(key=lambda av: ordem_data(av.data_disponibilizacao), reverse=True)
        return ResultadoAvisos(sucesso=sucesso, mensagens=mensagens, avisos=avisos)

    @staticmethod
    def interpretar_processo(conteudo):
        resposta = corpo_resposta(conteudo)
        sucesso, mensagens = _recibo(resposta)
        el = filho(resposta, 'processo')
        if el is None:
            return ResultadoProcesso(sucesso=sucesso, mensagens=mensagens)

        basicos = filho(el, 'dadosBasicos')
        # MNI 3.0: dadosBasicos (tipoCabecalhoProcesso) > dadosBasicos (tipoCabecalhoProcessoSimples) > numero
        processo = Processo(numero=(
            texto(basicos, 'dadosBasicos', 'numero') or texto(basicos, 'numero') or texto(el, 'numeroProcesso')
            or (basicos.get('numero', '') if basicos is not None else '')
        ))
        if basicos is not None:
            _cabecalho(processo, basicos)
        for m in filhos(el, 'movimento'):
            complementos = textos(m, 'complemento')
            descricao = _descricao_movimento(m)
            processo.movimentos.append(Movimento(
                data_hora=data(m.get('dataHora', '')),
                descricao=descricao,
                complementos=[c for c in complementos if c != descricao],
                id=m.get('idMovimento', ''),
                ids_documentos=textos(m, 'idDocumentoVinculado'),
            ))
        organizar_processo(processo, [_documento(d) for d in filhos(el, 'documento')])
        _aplicar_parametros(processo)
        return ResultadoProcesso(sucesso=sucesso, mensagens=mensagens, processo=processo)

    @staticmethod
    def interpretar_documentos(conteudo, content_type=''):
        """Aceita o conteúdo em base64 no XML ou como anexo MTOM (xop:Include)."""
        conteudo, anexos = separar_mtom(conteudo, content_type)
        resposta = corpo_resposta(conteudo)
        sucesso, mensagens = _recibo(resposta)
        documentos = [
            ConteudoDocumento(
                id=texto(d, 'idDocumento'), mimetype=texto(d, 'mimetype') or 'application/octet-stream',
                dados=conteudo_binario(filho(d, 'conteudo'), anexos), encoding=texto(d, 'encoding'),
            )
            for d in filhos(resposta, 'documentos')
        ]
        return ResultadoDocumentos(sucesso=sucesso, mensagens=mensagens, documentos=documentos)

    @staticmethod
    def interpretar_competencias(conteudo):
        """{código: descrição} das competências da localidade."""
        resposta = corpo_resposta(conteudo)
        sucesso, mensagens = _recibo(resposta)
        if not sucesso:
            raise ErroMNI('O tribunal não retornou as competências: ' + ' '.join(mensagens))
        return {
            texto(c, 'codigo'): texto(c, 'descricao')
            for c in filhos(resposta, 'competencias') if texto(c, 'codigo')
        }

    # --- competências --------------------------------------------------------------------------

    def consultar_competencias(self, codigo_localidade):
        """Tabela de competências da localidade (consultarCompetencias), com cache de 24 horas."""
        chave = f'mni:competencias:{self.ambiente.pk}:{codigo_localidade}'
        if (tabela := cache.get(chave)) is not None:
            return tabela
        corpo = self.envelope_consultar_competencias(codigo_localidade)
        tabela = self.interpretar_competencias(self._enviar('consultarCompetencias', corpo))
        if tabela:  # tabela vazia pode ser localidade de outra instância ou falha momentânea: não fixa em cache
            cache.set(chave, tabela, CACHE_TABELAS)
        return tabela

    def descrever_competencia(self, processo):
        """Preenche processo.competencia_descricao. Falhas não impedem a exibição do processo."""
        if not processo.competencia or not processo.codigo_localidade:
            return
        try:
            tabela = self.consultar_competencias(processo.codigo_localidade)
        except ErroMNI as exc:
            logger.warning('Competências indisponíveis (ambiente %s, localidade %s): %s',
                           self.ambiente.pk, processo.codigo_localidade, exc)
            return
        processo.competencia_descricao = tabela.get(processo.competencia, '')


# --- auxiliares do MNI 3.0 -------------------------------------------------------------------------

def _requisicao(nome, login, senha):
    env = Element(f'{{{NS_SOAP}}}Envelope')
    corpo = SubElement(env, f'{{{NS_SOAP}}}Body')
    req = SubElement(corpo, f'{{{NS_MNI}}}{nome}')
    consultante = SubElement(req, f'{{{NS_SERVICO}}}consultante')
    auth = SubElement(consultante, f'{{{NS_TIPOS}}}autenticacaoSimples')
    SubElement(auth, f'{{{NS_TIPOS}}}usuario').text = login
    SubElement(auth, f'{{{NS_TIPOS}}}senha').text = senha
    return env, req


def _recibo(resposta):
    recibo = filho(resposta, 'recibo')
    sucesso = texto(recibo, 'sucesso').lower() == 'true'
    mensagens = [texto(m, 'descritivo').lstrip('->').strip() for m in filhos(recibo, 'mensagens')]
    return sucesso, [m for m in mensagens if m]


def _campo(el, nome):
    """Valor de um campo que pode vir como elemento filho ou como atributo (variações entre tribunais)."""
    return texto(el, nome) or el.get(nome, '')


def _cabecalho(processo, basicos):
    processo.classe = texto(basicos, 'classeProcessual') or basicos.get('classeProcessual', '')
    processo.competencia = texto(basicos, 'competencia')
    processo.codigo_localidade = texto(basicos, 'codigoLocalidade')
    orgao = filho(basicos, 'orgaoJulgador')
    if orgao is not None:
        processo.orgao_julgador = texto(orgao, 'nome') or orgao.get('nomeOrgao', '')
    processo.data_ajuizamento = data(texto(basicos, 'dataAjuizamento') or basicos.get('dataAjuizamento', ''))
    processo.valor_causa = numero(texto(basicos, 'valorCausa'))
    processo.nivel_sigilo = numero(texto(basicos, 'nivelSigilo') or basicos.get('nivelSigilo', ''), int)
    processo.outros_numeros = textos(basicos, 'outrosNumeros')
    processo.prioridades = textos(basicos, 'prioridade')
    processo.parametros = [
        (o.get('nome', ''), o.get('valor', '') or (o.text or '').strip())
        for o in filhos(basicos, 'outroParametro') if o.get('nome')
    ]
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
            dados = filho(pessoa, 'dadosBasicos')
            origem = dados if dados is not None else pessoa
            polo.partes.append(Parte(
                nome=texto(origem, 'nome') or '—',
                documento=texto(origem, 'numeroDocumentoPrincipal'),
                tipo_pessoa=texto(origem, 'qualificacaoPessoa'),
                representantes=[
                    Representante(nome=a.get('nome', ''), inscricao=a.get('inscricao', ''),
                                  tipo=a.get('tipoRepresentante', ''))
                    for a in filhos(parte, 'advogado') if a.get('nome')
                ],
            ))
        processo.polos.append(polo)


def _documento(el) -> Documento:
    # O conteúdo (base64) é ignorado de propósito: só os dados do documento são usados.
    params = {p.get('nome'): p.get('valor', '') for p in filhos(el, 'outroParametro')}
    conteudo = filho(el, 'conteudo')
    return Documento(
        id=_campo(el, 'idDocumento'),
        descricao=_campo(el, 'descricao'),
        rotulo=params.get('rotulo', ''),
        data_hora=data(_campo(el, 'dataHora')),
        id_movimento=_campo(el, 'idMovimento'),
        nivel_sigilo=numero(_campo(el, 'nivelSigilo'), int),
        mimetype=texto(conteudo, 'mimetype'),
        tamanho=numero(params.get('tamanho') or _campo(el, 'tamanhoConteudo'), int),
        vinculados=[_documento(v) for v in filhos(el, 'documentoVinculado')],
    )


def _descricao_movimento(m):
    movimento_local = filho(m, 'movimentoLocal')
    if movimento_local is not None and movimento_local.get('descricao'):
        return movimento_local.get('descricao')
    complementos = textos(m, 'complemento')
    if complementos:
        return complementos[0]
    nacional = filho(m, 'movimentoNacional')
    if nacional is not None:
        return f"Movimento {nacional.get('codigoNacional', '')}".strip()
    return 'Movimento'


def _aplicar_parametros(processo):
    """Usa os outroParametro do eproc (com incluirDescricoes=true) para completar o processo."""
    params = {}
    for nome, valor in processo.parametros:
        params.setdefault(nome, valor)
    processo.classe_descricao = params.get('desClasse', '')
    processo.situacao = params.get('situacao', '')
    processo.prioridades_tribunal = [v for n, v in processo.parametros if n == 'prioridade' and v]
    if params.get('dataAutuacao'):
        processo.data_ajuizamento = data(params['dataAutuacao']) or processo.data_ajuizamento
    if not processo.orgao_julgador:
        processo.orgao_julgador = params.get('orgaoJulgador', '')
    # Descrição do assunto principal (o MNI só traz o código).
    if params.get('desAssunto'):
        principal = params.get('assuntoPrincipal', '')
        rotulo_assunto = f'Assunto {principal}'
        if principal and rotulo_assunto in processo.assuntos:
            processo.assuntos[processo.assuntos.index(rotulo_assunto)] = params['desAssunto']
        elif params['desAssunto'] not in processo.assuntos:
            processo.assuntos.insert(0, params['desAssunto'])
    for polo in processo.polos:
        polo.descricao = params.get(f'desTipoParte{polo.codigo}', '')
