from django.contrib import admin
from django.db.models import Count
from unfold.admin import ModelAdmin, TabularInline

from .models import Ambiente, SistemaJudicial

# Credencial e Preferencia NÃO são registradas aqui de propósito: pertencem somente ao
# seu dono e nem a administração pode vê-las ou alterá-las (só a contagem, abaixo).


class AmbienteInline(TabularInline):
    model = Ambiente
    fields = ['descricao', 'url_servico', 'ordem', 'ativo']
    extra = 0
    tab = True
    verbose_name = 'ambiente'
    verbose_name_plural = 'ambientes (URLs do serviço de integração)'


@admin.register(SistemaJudicial)
class SistemaJudicialAdmin(ModelAdmin):
    list_display = ['nome', 'descricao', 'total_ambientes', 'total_credenciais', 'ativo']
    list_filter = ['ativo']
    search_fields = ['nome', 'descricao']
    readonly_fields = ['criado_em', 'atualizado_em']
    inlines = [AmbienteInline]

    def get_queryset(self, request):
        # Contagens numa só consulta, em vez de duas por linha da listagem.
        return super().get_queryset(request).annotate(
            n_ambientes=Count('ambientes', distinct=True), n_credenciais=Count('credenciais', distinct=True),
        )

    @admin.display(description='ambientes', ordering='n_ambientes')
    def total_ambientes(self, obj):
        return obj.n_ambientes

    @admin.display(description='usuários cadastrados', ordering='n_credenciais')
    def total_credenciais(self, obj):
        return obj.n_credenciais


@admin.register(Ambiente)
class AmbienteAdmin(ModelAdmin):
    list_display = ['descricao', 'sistema', 'url_servico', 'ordem', 'ativo']
    list_filter = ['sistema', 'ativo']
    search_fields = ['descricao', 'url_servico', 'sistema__nome']
    list_select_related = ['sistema']
    autocomplete_fields = ['sistema']
    readonly_fields = ['criado_em', 'atualizado_em']
