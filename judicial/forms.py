from django import forms
from unfold.widgets import UnfoldAdminSelectWidget, UnfoldAdminTextInputWidget

from .models import Credencial, SistemaJudicial


class CredencialForm(forms.ModelForm):
    class Meta:
        model = Credencial
        fields = ['sistema', 'login']
        widgets = {
            'sistema': UnfoldAdminSelectWidget,
            'login': UnfoldAdminTextInputWidget,
        }

    def __init__(self, *args, usuario, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        sistemas = SistemaJudicial.objects.filter(ativo=True)
        if self.instance.pk:
            # Mantém o sistema atual na lista mesmo que tenha sido desativado.
            sistemas = SistemaJudicial.objects.filter(ativo=True) | SistemaJudicial.objects.filter(
                pk=self.instance.sistema_id
            )
        self.fields['sistema'].queryset = sistemas.distinct()
        self.fields['sistema'].empty_label = 'Selecione o sistema'

    def clean_login(self):
        return self.cleaned_data['login'].strip()

    def clean_sistema(self):
        sistema = self.cleaned_data['sistema']
        existe = Credencial.objects.filter(usuario=self.usuario, sistema=sistema).exclude(pk=self.instance.pk)
        if existe.exists():
            raise forms.ValidationError('Você já tem uma credencial cadastrada para este sistema.')
        return sistema

    def save(self, commit=True):
        self.instance.usuario = self.usuario
        return super().save(commit)
