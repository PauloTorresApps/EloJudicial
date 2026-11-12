FROM nginx:1.28-alpine

# openssl: gera um certificado autoassinado quando nenhum é fornecido.
RUN apk add --no-cache openssl \
    && rm -f /etc/nginx/conf.d/default.conf \
    && mkdir -p /run/nginx /var/www/acme

COPY nginx.conf /etc/nginx/nginx.conf
COPY templates/ /etc/nginx/templates/
COPY --chmod=755 preparar.sh /docker-entrypoint.d/05-preparar.sh
