# 0002. Execução em segundo plano com Celery

- **Status:** Aceita
- **Data:** 2026-11-23
- **Origem:** escolha do responsável pelo projeto, depois de comparar as opções

## Contexto
Um lote pode ter centenas ou milhares de documentos, e cada um é uma chamada MNI ao tribunal,
que pode levar segundos. O projeto não tinha nenhuma fila de tarefas. O passo seguinte ao da
ingestão é enviar os arquivos a um pipeline de agentes de IA, que também envolve chamadas
longas e externas.

## Decisão
A ingestão roda em segundo plano, com **Celery**, num serviço `worker` próprio no compose, que
consome a fila `ingestao`. A configuração fica em `config/celery.py` e nas variáveis `CELERY_*`
de `config/settings.py`.

## Alternativas consideradas
- **Síncrona, na própria requisição:** a mais simples, mas a requisição ficaria presa até
  baixar tudo, o que é inviável com dezenas de arquivos (timeouts) e impede o usuário de sair
  da página.
- **Fila própria no PostgreSQL** (as linhas dos arquivos como fila, com `SELECT … FOR UPDATE
  SKIP LOCKED`): sem dependência nova e com uma só fonte de verdade, mas exige escrever e manter
  o worker (pegar tarefas, novas tentativas, recuperar tarefas travadas, encerrar com segurança)
  e, depois, toda a orquestração dos agentes.
- **`django-tasks` com backend de banco:** a base do framework de tarefas do Django 6, com
  worker pronto e a fila no PostgreSQL. Dependência mais jovem, e a orquestração dos agentes
  continuaria por nossa conta.

## Motivos
O fator decisivo foi o pipeline de agentes de IA. O Celery já traz:
- orquestração (`chain` para etapas em sequência, `group` e `chord` para agentes em paralelo
  com uma etapa que junta os resultados);
- limite de taxa por tarefa (`rate_limit`), para respeitar os limites dos provedores de IA;
- novas tentativas com espera crescente para falhas temporárias;
- filas separadas com workers dimensionados para cada uma (por exemplo, `ingestao` e `ia`);
- limites de tempo por tarefa e ferramentas de monitoramento (Flower).

Para a ingestão sozinha, a fila própria no PostgreSQL bastaria. Mas ela obrigaria a reescrever
depois, à mão, o que o Celery oferece pronto para os agentes.

## Consequências
- Dois serviços novos no compose: `worker` e `redis-fila` (ver [0003](0003-redis-separado-para-a-fila.md)).
- Fora do Docker e nos testes, sem `REDIS_FILA_URL`, as tarefas rodam na hora, no próprio
  processo (`CELERY_TASK_ALWAYS_EAGER`).
- O Celery executa as tarefas, mas não guarda o estado do pipeline (ver
  [0004](0004-estado-no-postgresql-mensagens-so-com-ids.md)).
- Tarefas longas pedem cuidados específicos com o Redis como broker (ver
  [0006](0006-tarefas-longas-e-idempotencia.md)).
- Se um dia o pipeline precisar de fluxos que duram dias, retomada no meio de uma etapa ou
  aprovação humana entre etapas, vale avaliar um motor de workflow (por exemplo, Temporal).
