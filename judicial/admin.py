from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import Ambiente, Credencial, Preferencia, SistemaJudicial


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

    @admin.display(description='ambientes')
    def total_ambientes(self, obj):
        return obj.ambientes.count()

    @admin.display(description='credenciais')
    def total_credenciais(self, obj):
        return obj.credenciais.count()


@admin.register(Ambiente)
class AmbienteAdmin(ModelAdmin):
    list_display = ['descricao', 'sistema', 'url_servico', 'ordem', 'ativo']
    list_filter = ['sistema', 'ativo']
    search_fields = ['descricao', 'url_servico', 'sistema__nome']
    list_select_related = ['sistema']
    autocomplete_fields = ['sistema']
    readonly_fields = ['criado_em', 'atualizado_em']


@admin.register(Credencial)
class CredencialAdmin(ModelAdmin):
    list_display = ['usuario', 'sistema', 'login', 'atualizado_em']
    list_filter = ['sistema']
    search_fields = ['login', 'usuario__username', 'usuario__email', 'sistema__nome']
    autocomplete_fields = ['usuario', 'sistema']
    list_select_related = ['usuario', 'sistema']
    readonly_fields = ['criado_em', 'atualizado_em']


@admin.register(Preferencia)
class PreferenciaAdmin(ModelAdmin):
    list_display = ['usuario', 'ambiente_padrao', 'atualizado_em']
    list_filter = ['ambiente_padrao__sistema']
    search_fields = ['usuario__username', 'usuario__email']
    autocomplete_fields = ['usuario']
    list_select_related = ['usuario', 'ambiente_padrao__sistema']
