# 0006. Configuração para tarefas longas e idempotência

- **Status:** Aceita
- **Data:** 2026-11-23
- **Origem:** proposta técnica aceita

## Contexto
Downloads do tribunal e, depois, chamadas a modelos de IA são tarefas longas. Com o Redis como
broker, uma tarefa que passa do `visibility_timeout` (1 h por padrão) é entregue de novo a outro
worker. Na ingestão isso baixaria o arquivo duas vezes; na IA, pagaria a chamada duas vezes.
Uma queda do worker no meio da tarefa também não pode fazer a tarefa sumir.

## Decisão
Em `config/settings.py`:
- `CELERY_TASK_ACKS_LATE = True`: a mensagem só é confirmada ao fim da tarefa; se o worker cair,
  ela volta para a fila.
- `CELERY_TASK_REJECT_ON_WORKER_LOST = True`: idem quando o processo do worker morre.
- `CELERY_WORKER_PREFETCH_MULTIPLIER = 1`: cada processo pega uma tarefa por vez, sem reservar
  outras enquanto trabalha numa longa.
- `CELERY_BROKER_TRANSPORT_OPTIONS = {'visibility_timeout': 2 h}`: acima da tarefa mais longa
  prevista.

E a tarefa `ingerir_arquivo` é **idempotente**: começa mudando a situação de "na fila" ou
"baixando" para "baixando" numa só atualização no banco. Se o arquivo já estiver pronto, sem
texto, com erro ou aguardando senha, a tarefa termina sem fazer nada.

Falhas de comunicação com o tribunal (`ErroMNI`) são tentadas de novo até 3 vezes, com espera
crescente (30 s, 60 s, 120 s). Depois disso, o arquivo fica com erro e a mensagem do tribunal.
Resposta grande demais não é tentada de novo, porque o resultado seria o mesmo.

## Alternativas consideradas
- **Padrão do Celery (confirmação no início, prefetch 4):** uma queda perde a tarefa, e um
  processo reserva tarefas que outros poderiam executar.
- **Trava distribuída por arquivo:** mais complexa; a atualização condicional no banco já
  impede o reprocessamento de um arquivo concluído.

## Motivos
Garantir que nenhuma tarefa se perca e que uma tarefa repetida não cause trabalho nem custo em
dobro.

## Consequências
- A situação "baixando" também é aceita no início da tarefa, para retomar arquivos de um worker
  que caiu. Se duas cópias da mesma mensagem rodarem ao mesmo tempo, as duas baixam e gravam o
  mesmo conteúdo: o resultado não muda, apenas o trabalho é repetido.
- Novas tarefas longas (como as dos agentes de IA) devem seguir a mesma regra: ser idempotentes
  e caber dentro do `visibility_timeout`.
