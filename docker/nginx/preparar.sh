#!/bin/sh
# Executado pelo entrypoint oficial do nginx antes de gerar a configuração.
set -eu

CERTS=/etc/nginx/certs
mkdir -p "$CERTS"

# 1) Certificado: sem fullchain.pem/privkey.pem montados, gera um autoassinado.
if [ ! -s "$CERTS/fullchain.pem" ] || [ ! -s "$CERTS/privkey.pem" ]; then
    echo "$0: certificado não encontrado; gerando um autoassinado para $NGINX_SERVER_NAME"
    openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
        -keyout "$CERTS/privkey.pem" -out "$CERTS/fullchain.pem" \
        -subj "/CN=$NGINX_SERVER_NAME" \
        -addext "subjectAltName=DNS:$NGINX_SERVER_NAME,DNS:$NGINX_DB_HOST,DNS:$NGINX_REDIS_HOST,DNS:localhost,IP:127.0.0.1" \
        2>/dev/null
    chmod 600 "$CERTS/privkey.pem"
fi

# 2) Quem pode acessar o banco e o Redis pela 443 (lista separada por vírgulas).
#    Vazio = ninguém (apenas o sistema, pela rede interna do Docker).
ACL=/etc/nginx/servicos-permitidos.conf
: > "$ACL"
for origem in $(echo "${NGINX_SERVICOS_PERMITIDOS:-}" | tr ',' ' '); do
    echo "allow $origem;" >> "$ACL"
done
echo "deny all;" >> "$ACL"
