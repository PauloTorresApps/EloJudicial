# 0003. Redis separado para a fila, com persistência

- **Status:** Aceita
- **Data:** 2026-11-23
- **Origem:** proposta técnica aceita, a partir de uma pergunta do responsável pelo projeto

## Contexto
O Redis do projeto roda sem persistência (`--save ""` e `--appendonly no`) de propósito: guarda
temporariamente dados sensíveis, como o hash da senha dos sistemas judiciais, que para o MNI
equivale à própria senha.

O Celery usa o Redis como fila. Sem persistência, um restart do container apagaria os lotes
pendentes sem aviso. A pergunta foi se daria para persistir só os dados da fila e não os
sensíveis.

## Decisão
Um segundo Redis, `redis-fila`, só para a fila do Celery:
- com persistência (AOF, `--appendonly yes`) e um volume próprio (`redis-fila`);
- com `--maxmemory-policy noeviction`, para nunca descartar mensagens;
- com senha própria (`REDIS_FILA_PASSWORD`) e acessível só pela rede interna do compose.

O Redis atual continua sem persistência e segue guardando só o que é temporário.

## Alternativas consideradas
- **Ligar a persistência no Redis atual:** não serve. No Redis, a persistência vale para a
  instância inteira; não é possível escolher chaves ou bancos lógicos que vão ou não para o
  disco. Os hashes de senha seriam gravados em disco, mesmo com TTL.
- **Um único Redis com bancos lógicos diferentes (0 e 1):** mesmo problema; os bancos lógicos
  dividem a mesma configuração de persistência.
- **Fila sem persistência:** aceitar perder os jobs pendentes a cada restart.

## Motivos
- Mantém a regra de que dados sensíveis nunca vão para o disco.
- Um broker de fila e um cache pedem configurações opostas quando a memória enche: o cache pode
  descartar chaves; a fila não pode, ou perde tarefas. Separados, cada um usa a regra certa.

## Consequências
- Mais um container leve no compose e uma variável de ambiente (`REDIS_FILA_URL`).
- Mesmo persistida, a fila nunca recebe senha, hash ou conteúdo de documento (ver
  [0004](0004-estado-no-postgresql-mensagens-so-com-ids.md) e [0005](0005-acesso-ao-tribunal-pelo-worker.md)).
- Em produção é preciso definir `REDIS_FILA_PASSWORD` no `.env`.
