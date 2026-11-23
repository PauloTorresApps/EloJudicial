"""Seleção inicial dos documentos na tabela de eventos do processo.

Cada documento (inclusive vinculado) tem um toggle, que vem marcado quando o tipo local
do documento está na lista do sistema judicial (ver TipoDocumentoMarcado).
"""


def selecao_inicial(processo, codigos_marcados: set[str]) -> dict:
    """Contexto da seleção: ids que vêm marcados, quantos toggles vêm marcados e o total de toggles.

    Conta os vinculados e cada aparição do documento na tabela (um documento pode estar em mais de
    um evento), para bater com o que a tela exibe.
    """
    documentos = [d for mov in processo.movimentos for d in mov.documentos]
    for doc in documentos:  # a lista cresce com os vinculados durante o laço
        documentos.extend(doc.vinculados)
    marcados = [d for d in documentos if d.tipo_local in codigos_marcados]
    return {
        'documentos_marcados': {d.id for d in marcados},
        'arquivos_selecionados': len(marcados),
        'total_arquivos': len(documentos),
    }
