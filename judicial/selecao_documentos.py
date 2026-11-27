"""Seleção dos documentos na tabela de eventos do processo.

Cada documento (inclusive vinculado) tem um toggle, que vem marcado quando o tipo local
do documento está na lista do sistema judicial (ver TipoDocumentoMarcado). Os marcados
podem ser enviados para a ingestão (app ingestao).
"""

from django.conf import settings
from django.core.cache import cache


def documentos_da_tabela(processo):
    """Documentos na ordem em que aparecem na tabela, com os vinculados e as repetições
    (um documento pode estar em mais de um evento)."""
    documentos = [d for mov in processo.movimentos for d in mov.documentos]
    for doc in documentos:  # a lista cresce com os vinculados durante o laço
        documentos.extend(doc.vinculados)
    return documentos


def selecao_inicial(documentos, codigos_marcados: set[str]) -> dict:
    """Contexto da seleção: ids que vêm marcados, quantos toggles vêm marcados e o total de toggles."""
    marcados = [d for d in documentos if d.tipo_local in codigos_marcados]
    return {
        'documentos_marcados': {d.id for d in marcados},
        'arquivos_selecionados': len(marcados),
        'total_arquivos': len(documentos),
    }


# --- documentos exibidos (para a ingestão dos selecionados) --------------------------------
# Guardados no cache (Redis sem persistência) com o prazo da senha: a ingestão usa nomes e
# tipos sem consultar o processo inteiro de novo, e só aceita ids que estavam na tabela.

def _chave(usuario, ambiente, numero):
    return f'processo:documentos:{usuario.pk}:{ambiente.pk}:{numero}'


def eventos_dos_documentos(processo) -> dict[str, int]:
    """{id do documento: número do evento}. O vinculado fica com o evento do principal; o
    documento que aparece em mais de um evento fica com o menor (o da juntada)."""
    eventos = {}
    for mov in processo.movimentos:
        pendentes = list(mov.documentos)
        for doc in pendentes:  # a lista cresce com os vinculados durante o laço
            pendentes.extend(doc.vinculados)
            if doc.id and mov.numero and (doc.id not in eventos or mov.numero < eventos[doc.id]):
                eventos[doc.id] = mov.numero
    return eventos


def lembrar_documentos(usuario, ambiente, numero, processo):
    eventos = eventos_dos_documentos(processo)
    cache.set(
        _chave(usuario, ambiente, numero),
        {d.id: (d.nome, d.descricao, d.tipo_local, eventos.get(d.id)) for d in documentos_da_tabela(processo) if d.id},
        settings.MNI_SENHA_TTL,
    )


def documentos_lembrados(usuario, ambiente, numero):
    """{id: (nome, descrição, tipo local, número do evento)} da última exibição do processo, ou
    None se expirou. Itens guardados antes do número do evento têm só os três primeiros campos."""
    return cache.get(_chave(usuario, ambiente, numero))
