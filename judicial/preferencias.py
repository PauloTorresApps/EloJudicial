from django.urls import reverse

from .models import Ambiente, Preferencia


def ambiente_padrao(usuario):
    """Ambiente padrão do usuário, se ainda estiver ativo (ambiente e sistema)."""
    if not usuario.is_authenticated:
        return None
    return (
        Ambiente.objects.select_related('sistema')
        .filter(
            pk=Preferencia.objects.filter(usuario=usuario).values('ambiente_padrao')[:1],
            ativo=True, sistema__ativo=True,
        )
        .first()
    )


def definir_ambiente_padrao(usuario, ambiente):
    Preferencia.objects.update_or_create(usuario=usuario, defaults={'ambiente_padrao': ambiente})


def url_ambiente_padrao(usuario):
    """URL da tela de serviços do ambiente padrão, ou None."""
    ambiente = ambiente_padrao(usuario)
    if ambiente is None:
        return None
    return reverse('sistemas:servicos', args=[ambiente.sistema_id, ambiente.pk])
