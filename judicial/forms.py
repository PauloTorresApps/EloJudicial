from django import forms
from unfold.widgets import UnfoldAdminTextInputWidget

from .models import Credencial


class CredencialForm(forms.ModelForm):
    """Usuário de acesso a um sistema judicial. O sistema vem da página (um por sistema)."""

    class Meta:
        model = Credencial
        fields = ['login']
        widgets = {'login': UnfoldAdminTextInputWidget}

    def __init__(self, *args, usuario, sistema, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.usuario = usuario
        self.instance.sistema = sistema

    def clean_login(self):
        return self.cleaned_data['login'].strip()
