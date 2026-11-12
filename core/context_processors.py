from django.contrib import admin

from unfold.widgets import BASE_CLASSES

from .forms import INPUT_CLASSES

CLASSES_INPUT_BASE = ' '.join(BASE_CLASSES)  # sem padding/largura, para campos com ícone


def unfold(request):
    """Disponibiliza o contexto do tema Unfold (cores, títulos, tema claro/escuro,
    favicons etc.) para as páginas da área do usuário.

    As views do admin já recebem esse contexto do próprio AdminSite.
    """
    match = getattr(request, 'resolver_match', None)
    if match is not None and match.namespace == 'admin':
        return {}
    numero_busca = request.session.pop('busca_processo', '') if hasattr(request, 'session') else ''
    return {
        **admin.site.each_context(request),
        'classes_input': INPUT_CLASSES,
        'classes_input_base': CLASSES_INPUT_BASE,
        'numero_busca': numero_busca,  # número inválido digitado na barra (para correção)
    }
