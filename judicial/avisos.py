"""Consulta de avisos pendentes com registro de tudo o que o tribunal devolve.

O consultarAvisosPendentes do MNI 3.0 entrega cada aviso uma única vez: depois de
consultado, ele deixa de ser devolvido. Por isso a resposta bruta é gravada assim que
chega (antes de ser interpretada) e cada aviso vira um registro do usuário, marcado
como pendente até ser tratado.
"""
import logging

from django.db import transaction

from . import mni
from .models import AvisoPendente, ConsultaAvisos

logger = logging.getLogger(__name__)


def consultar_e_registrar(usuario, ambiente, login, senha_hash):
    """Consulta os avisos pendentes e grava resposta e avisos. Retorna (resultado, pks dos avisos novos)."""
    consulta = None

    def guardar_resposta(conteudo):
        nonlocal consulta
        consulta = ConsultaAvisos.objects.create(
            usuario=usuario, ambiente=ambiente, login=login, resposta=bytes(conteudo),
        )

    resultado = mni.consultar_avisos_pendentes(ambiente, login, senha_hash, ao_receber=guardar_resposta)
    novos = set()
    if consulta is None:  # nada chegou do tribunal
        return resultado, novos

    with transaction.atomic():
        consulta.sucesso = resultado.sucesso
        consulta.mensagens = '\n'.join(resultado.mensagens)
        consulta.total_avisos = len(resultado.avisos)
        consulta.save(update_fields=['sucesso', 'mensagens', 'total_avisos'])
        for aviso in resultado.avisos:
            dados = {
                'consulta': consulta, 'login': login, 'tipo_codigo': aviso.tipo_codigo[:10],
                'numero_processo': aviso.numero_processo[:30], 'destinatario': aviso.destinatario[:500],
                'orgao_julgador': aviso.orgao_julgador[:500], 'data_disponibilizacao': aviso.data_disponibilizacao,
                'tipo_prazo': aviso.tipo_prazo[:20], 'prazo': aviso.prazo, 'xml': aviso.xml,
            }
            if not aviso.id:
                obj, criado = AvisoPendente.objects.create(usuario=usuario, ambiente=ambiente, **dados), True
            else:
                # Se o tribunal devolver de novo um aviso já guardado, mantém o registro original.
                obj, criado = AvisoPendente.objects.get_or_create(
                    usuario=usuario, ambiente=ambiente, id_aviso=aviso.id[:100], defaults=dados,
                )
            if criado:
                novos.add(obj.pk)
    if novos:
        logger.info('%s aviso(s) pendente(s) registrado(s) (consulta #%s).', len(novos), consulta.pk)
    return resultado, novos


def ultima_consulta(usuario, ambiente):
    """Data da última consulta ao tribunal que trouxe resposta (do próprio usuário)."""
    return (
        ConsultaAvisos.objects.filter(usuario=usuario, ambiente=ambiente)
        .order_by('-realizada_em').values_list('realizada_em', flat=True).first()
    )


def avisos_guardados(usuario, ambiente):
    """Avisos ainda pendentes do usuário neste ambiente — somente os dele."""
    return AvisoPendente.objects.filter(usuario=usuario, ambiente=ambiente, pendente=True)
