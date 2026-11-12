from django.utils.cache import add_never_cache_headers, patch_vary_headers


class SemCacheParaUsuarioLogado:
    """Páginas de usuário logado nunca são guardadas em cache (navegador ou proxy).

    Evita que dados de um usuário reapareçam para outra pessoa no mesmo computador
    (botão voltar após sair) ou sejam reaproveitados por um cache intermediário.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if getattr(request, 'user', None) is not None and request.user.is_authenticated:
            add_never_cache_headers(response)  # private, no-cache, no-store, must-revalidate
            patch_vary_headers(response, ['Cookie'])
        return response
