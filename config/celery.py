"""Aplicação Celery: tarefas em segundo plano (ingestão de documentos e, depois, os agentes de IA).

A fila fica num Redis próprio, com persistência (REDIS_FILA_URL). As mensagens carregam só
identificadores: nunca senha, hash de senha ou conteúdo de documento. O estado de cada tarefa
fica no PostgreSQL, nas tabelas de cada módulo.
"""

import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('elojudicial')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
