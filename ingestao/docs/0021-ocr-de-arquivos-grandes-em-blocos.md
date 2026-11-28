# 0021. OCR de arquivos grandes em blocos de páginas

- **Status:** Proposta
- **Data:** 2026-11-27
- **Origem:** proposta técnica
- **Complementa:** [0018](0018-ocr-em-modulo-e-worker-proprios.md) e [0020](0020-situacao-reconhecendo-durante-o-ocr.md)

## Contexto
O `reconhecer_arquivo` lia numa só tarefa todas as páginas do arquivo que precisam de OCR. O
Redis da fila entrega de novo a mensagem que não é confirmada em 2 h (`visibility_timeout`,
[0006](0006-tarefas-longas-e-idempotencia.md)). Pelos tempos da [0019](0019-motores-de-ocr-em-dois-niveis.md),
cerca de 1,4 s por página no Tesseract e cerca de 5,5 s quando a página vai também ao RapidOCR,
um arquivo digitalizado com mais de umas 1.300 páginas passaria das 2 h. Com isso:
- a mensagem seria entregue a um segundo worker enquanto o primeiro ainda lê o arquivo. Os dois
  leriam as mesmas páginas pendentes, em dobro;
- o processo de um arquivo enorme ficaria preso por horas, e os outros arquivos da fila `ocr`
  esperariam atrás dele.

## Decisão
- **Blocos de `PAGINAS_POR_TAREFA` (50) páginas.** Cada execução do `reconhecer_arquivo` lê até
  50 páginas pendentes, na ordem. Se ainda sobram páginas, grava o arquivo como "reconhecendo",
  com a mensagem atualizada ("N de M páginas na fila do OCR"), e põe uma nova mensagem com o mesmo
  id no **fim** da fila `ocr`. No pior caso, um bloco leva cerca de 5 min.
- **A continuação não precisa de estado novo.** As páginas pendentes já são o estado: cada bloco
  pega as que continuam em `PRECISAM_OCR`. A mensagem continua levando só o id do arquivo
  ([0004](0004-estado-no-postgresql-mensagens-so-com-ids.md)).
- **O reaproveitamento por SHA-256 não conta no limite:** as páginas copiadas de outro lote não
  rodam o motor e são gravadas todas no primeiro bloco.
- **Falha:** como na [0020](0020-situacao-reconhecendo-durante-o-ocr.md), a exceção interrompe a
  sequência e o arquivo sai de "reconhecendo" pela situação das páginas. As já lidas ficam.
- **Limite de tempo como proteção:** a tarefa tem `soft_time_limit` de 30 min e `time_limit` de
  35 min, bem abaixo das 2 h. O limite brando levanta `SoftTimeLimitExceeded` dentro da tarefa
  (pool prefork), o que aciona a mesma saída da falha. O rígido só mata o processo que não
  responder. Nesse caso o arquivo pode ficar em "reconhecendo" e volta à fila com
  `reextrair --situacao reconhecendo`.

## Alternativas consideradas
- **Uma tarefa por faixa de páginas, todas enfileiradas de uma vez, com o `chord` do Celery para
  fechar a situação do arquivo:** leria o mesmo arquivo em paralelo, mas o `chord` exige um backend
  de resultados, e o projeto guarda o estado no PostgreSQL (`CELERY_TASK_IGNORE_RESULT`,
  [0004](0004-estado-no-postgresql-mensagens-so-com-ids.md)).
- **Faixas em paralelo com um contador de faixas pendentes no arquivo:** a última faixa a terminar
  fecharia a situação. Exige uma coluna nova e um decremento que não pode contar duas vezes a
  mesma faixa quando a mensagem é entregue de novo. Também complica a falha: a faixa que falha
  enquanto as outras rodam não pode tirar o arquivo de "reconhecendo".
- **Aumentar o `visibility_timeout`:** só adia o problema. Uma mensagem perdida por queda do worker
  demoraria ainda mais para voltar, e a fila continuaria presa atrás do arquivo enorme.
- **Bloco por tempo em vez de número de páginas:** limitaria melhor as páginas lentas, mas um bloco
  fixo é mais simples de prever e de testar, e 50 páginas já ficam longe do limite.

## Motivos
O bloco com continuação no fim da fila resolve os dois problemas sem coluna nova nem backend de
resultados: nenhuma tarefa chega perto das 2 h, e um arquivo enorme divide o worker com os outros
em vez de bloqueá-lo. As páginas pendentes já guardam o estado, então uma queda no meio de um bloco
retoma do ponto onde parou.

## Consequências
- Um arquivo não é lido em paralelo por mais de um processo. A vazão vem de arquivos diferentes ao
  mesmo tempo (`OCR_CONCORRENCIA`, padrão 4).
- Num lote com um arquivo enorme e vários pequenos, os pequenos terminam antes. O enorme anda em
  blocos intercalados com eles.
- O PDF é aberto de novo a cada bloco: um custo pequeno, porque o pdfium lê sob demanda.
- A mensagem do arquivo em "reconhecendo" mostra o progresso, bloco a bloco, quando a tela do lote
  é recarregada.
