# 0004. Estado no PostgreSQL; mensagens da fila só com ids

- **Status:** Aceita
- **Data:** 2026-11-23
- **Origem:** proposta técnica aceita

## Contexto
Com o Celery há dois lugares onde o andamento de uma tarefa poderia ficar: a própria fila (e o
backend de resultados do Celery) e o banco da aplicação. A tela precisa mostrar o andamento de
cada arquivo, e será preciso reprocessar arquivos que falharam. Além disso, a fila é persistida
em disco (ver [0003](0003-redis-separado-para-a-fila.md)).

## Decisão
- A situação de cada arquivo fica no PostgreSQL, no campo `ArquivoIngerido.situacao`: na fila,
  baixando, pronto, sem texto, aguardando senha ou erro, com uma mensagem para o usuário.
- As mensagens da fila levam só o id do arquivo. Nunca senha, hash de senha, sessão ou conteúdo
  de documento.
- O backend de resultados do Celery fica desligado (`CELERY_TASK_IGNORE_RESULT = True`).

## Alternativas consideradas
- **Status no backend de resultados do Celery:** cria uma segunda fonte de verdade, que pode
  divergir do banco (por exemplo, a tarefa entra na fila e a transação do banco é desfeita).
- **Mandar os dados necessários na mensagem** (nome, tipo, credenciais): evitaria consultas ao
  banco no worker, mas colocaria dados de processo e credenciais num armazenamento persistido.

## Motivos
- Uma única fonte de verdade: a tela, os relatórios e o reprocessamento leem as mesmas linhas.
- Reprocessar é uma atualização no banco seguida de novo enfileiramento.
- Nada sensível chega ao disco do Redis da fila.

## Consequências
- O worker consulta o banco no início de cada tarefa.
- As tarefas são enfileiradas só depois do commit (`transaction.on_commit`), para que o worker
  enxergue os registros que acabaram de ser criados.
- Para os agentes de IA, a mesma regra vale: resultados e etapas em tabelas próprias. O backend
  de resultados do Celery só seria ligado para coordenar `chord`s, e no `redis-fila`.
