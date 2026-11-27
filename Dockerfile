# syntax=docker/dockerfile:1
# Duas imagens a partir da mesma base:
# - "ocr": worker da fila "ocr", com Tesseract e RapidOCR (ingestao/docs/0018 e 0019);
# - "app" (a última, padrão do `docker build`): web e worker da fila "ingestao".
FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY requirements.txt .
# Cache do pip fora da imagem (BuildKit): um rebuild só baixa o que mudou no requirements.txt.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt


FROM base AS ocr

# Cache do apt fora da imagem, como o do pip.
RUN rm -f /etc/apt/apt.conf.d/docker-clean
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-por

COPY requirements-ocr.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements-ocr.txt

# Modelos do RapidOCR baixados no build: em execução, o worker não depende da internet.
COPY ocr/ ocr/
RUN python -m ocr

COPY --chown=app:app . .
RUN mkdir -p /var/lib/elojudicial/ingestao && chown -R app:app /var/lib/elojudicial

# Uma thread por processo do Tesseract; o paralelismo vem da concorrência do worker.
ENV OMP_THREAD_LIMIT=1

USER app

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["celery", "-A", "config", "worker", "-Q", "ocr", "--loglevel", "INFO"]


FROM base AS app

COPY . .
RUN chmod +x docker/entrypoint.sh \
    && DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && mkdir -p /var/lib/elojudicial/ingestao \
    && chown -R app:app /app /var/lib/elojudicial

USER app

EXPOSE 8000

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-"]
