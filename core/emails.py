import logging

from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .tokens import ativacao_token

logger = logging.getLogger(__name__)


def enviar_confirmacao_email(request, user):
    """Envia o link de confirmação. Retorna True se o envio foi aceito pelo servidor."""
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = ativacao_token.make_token(user)
    link = request.build_absolute_uri(reverse('ativar_conta', args=[uid, token]))
    contexto = {'user': user, 'link': link}

    assunto = render_to_string('contas/emails/confirmacao_assunto.txt', contexto).strip()
    corpo = render_to_string('contas/emails/confirmacao.txt', contexto)
    try:
        send_mail(assunto, corpo, None, [user.email])
    except Exception:
        logger.exception('Falha ao enviar e-mail de confirmação para o usuário %s', user.pk)
        return False
    return True
