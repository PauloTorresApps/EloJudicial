from django import forms
from django.contrib.auth import forms as auth_forms
from django.contrib.auth import get_user_model
from unfold.widgets import BASE_INPUT_CLASSES

INPUT_CLASSES = ' '.join(BASE_INPUT_CLASSES)


class UnfoldFormMixin:
    """Aplica nos campos as mesmas classes CSS dos inputs do Unfold."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if getattr(field.widget, 'input_type', None) not in ('checkbox', 'radio', 'hidden'):
                field.widget.attrs['class'] = INPUT_CLASSES


class LoginForm(UnfoldFormMixin, auth_forms.AuthenticationForm):
    error_messages = {
        **auth_forms.AuthenticationForm.error_messages,
        'invalid_login': 'Usuário, e-mail ou senha incorretos. A senha diferencia maiúsculas de minúsculas.',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = 'Usuário ou e-mail'


class CadastroForm(UnfoldFormMixin, auth_forms.UserCreationForm):
    first_name = forms.CharField(label='Nome', max_length=150)
    last_name = forms.CharField(label='Sobrenome', max_length=150)
    email = forms.EmailField(label='E-mail')

    class Meta(auth_forms.UserCreationForm.Meta):
        model = get_user_model()
        fields = ('first_name', 'last_name', 'email', 'username')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].help_text = 'Até 150 caracteres. Letras, números e . + - _ apenas.'

    def clean_username(self):
        username = super().clean_username()
        if '@' in username:
            raise forms.ValidationError('O nome de usuário não pode conter "@". Use letras, números e . + - _')
        return username

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Já existe um usuário cadastrado com este e-mail.')
        return email


class ReenviarConfirmacaoForm(UnfoldFormMixin, forms.Form):
    email = forms.EmailField(label='E-mail')


class RecuperarSenhaForm(UnfoldFormMixin, auth_forms.PasswordResetForm):
    pass


class NovaSenhaForm(UnfoldFormMixin, auth_forms.SetPasswordForm):
    pass


class AlterarSenhaForm(UnfoldFormMixin, auth_forms.PasswordChangeForm):
    pass
