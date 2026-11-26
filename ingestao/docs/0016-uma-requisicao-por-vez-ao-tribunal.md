# 0016. Uma requisição por vez a cada tribunal, com intervalo mínimo

- **Status:** Aceita
- **Data:** 2026-11-25
- **Origem:** escolha do responsável pelo projeto

## Contexto
Os tribunais podem bloquear o IP de quem dispara muitas requisições ao serviço MNI num intervalo
curto. Um bloqueio derrubaria o EloJudicial naquele tribunal para todos os usuários, e não há como
revertê-lo pelo nosso lado.

O sistema chamava o tribunal sem nenhuma coordenação:
- o worker baixava 4 arquivos em paralelo (`--concurrency 4`), cada um na sua requisição;
- abrir um processo faz até 3 requisições seguidas (`consultarProcesso`, a repetição sem
  documentos quando a resposta é grande demais e `consultarCompetencias`);
- os 3 processos do gunicorn atendem usuários diferentes ao mesmo tempo, e cada um chama o
  tribunal por conta própria;
- o download do WSDL também é uma requisição ao tribunal.

Este ponto estava em "Decisões em aberto" ("Carga no tribunal"), previsto só "se algum tribunal
reclamar". Passou a ser regra antes de qualquer reclamação.

## Decisão
**Regra do projeto:** nenhum código pode disparar várias requisições ao MNI num intervalo curto,
nem em paralelo, nem em sequência rápida (laços, divisão de lotes, novas tentativas imediatas).
Paralelismo é permitido só no que não toca o tribunal (ex.: extração de texto de arquivos já
baixados). Quando for preciso obter vários itens, prefere-se uma requisição com vários itens a
várias requisições (ver [0017](0017-download-do-lote-numa-unica-requisicao.md)).

**Mecanismo** (`judicial/limitador.py`, `vez_do_tribunal(url)`):
- **Uma requisição por vez por tribunal**, com uma trava no Redis de cache, compartilhado pelo
  servidor web e pelo worker. A trava vale entre todos os processos.
- **Intervalo mínimo de 2 s** entre o fim de uma requisição e o início da próxima ao mesmo
  tribunal (`MNI_INTERVALO`, configurável no `.env`).
- **"Tribunal" = os 3 últimos rótulos do host** (`eproc1g-ws.tjto.jus.br`,
  `eproc2g.hmlg.tjto.jus.br` → `tjto.jus.br`). Produção, homologação, 1º e 2º grau contam juntos,
  porque o bloqueio costuma valer para a infraestrutura do tribunal inteiro.
- **Aplicado no transporte:** `ClienteMNI._enviar_bruto`, por onde passam todas as operações SOAP,
  e `wsdl._baixar`. Nenhuma chamada nova ao tribunal pode contornar esses dois pontos.
- **Espera máxima pela vez:** 20 s nas telas, abaixo dos 30 s do gunicorn. Na ingestão são 3 min,
  com `limitador.paciencia(...)`. Esgotada a espera, a tela mostra "O tribunal está atendendo
  outras consultas do EloJudicial", e a ingestão trata isso como falha de comunicação: nova
  tentativa com espera crescente.
- **Trava com prazo de 5 min:** se o processo morrer no meio de uma requisição, a trava se
  desfaz sozinha. Cada processo só apaga a própria trava.

## Alternativas consideradas
- **`rate_limit` do Celery por tarefa:** vale por processo do worker, não entre processos nem
  para o servidor web. Não impede requisições simultâneas. Descartada.
- **Worker da ingestão com `--concurrency 1`:** resolve só a ingestão. As telas continuariam
  chamando o tribunal em paralelo com o worker. Também perderia o paralelismo da extração de
  texto, que não toca o tribunal.
- **Fila única para todas as chamadas ao tribunal (inclusive as das telas):** serializaria tudo,
  mas as telas precisam da resposta na hora. Exigiria esperar o resultado de uma tarefa dentro da
  requisição web, o que é mais complexo e frágil que uma trava.
- **Limite por host exato:** 1º e 2º grau do mesmo tribunal poderiam ser chamados ao mesmo tempo,
  e o bloqueio por IP costuma ser do tribunal inteiro.
- **Trava nativa do Redis (`redis.lock`):** prenderia o código ao Redis. `cache.add` é atômico
  no Redis e também funciona com o cache em memória dos testes.

## Motivos
Um único ponto de controle, no transporte, cobre todos os caminhos atuais e futuros (telas,
worker, WSDL), sem depender de cada chamador lembrar da regra. O custo, alguns segundos a mais
por operação, é pequeno perto do risco de ter o IP bloqueado.

## Consequências
- Abrir um processo pode levar até cerca de 4 s a mais (até 3 requisições espaçadas).
- Usuários do mesmo tribunal esperam uns pelos outros. Em pico, a tela pode pedir para tentar de
  novo em instantes.
- A trava não é reentrante: não chamar `vez_do_tribunal` dentro de outro `vez_do_tribunal` para o
  mesmo tribunal, senão o processo espera por si mesmo até esgotar o prazo.
- Nos testes, `MNI_INTERVALO = 0`. Os testes do limitador (`judicial/tests/test_limitador.py`)
  ligam o intervalo e conferem a espera, o "tribunal ocupado" e que MNI e WSDL passam pelo
  limitador.
- Se um tribunal exigir mais folga, basta aumentar `MNI_INTERVALO`. Um intervalo por tribunal
  exigiria um campo novo no cadastro do sistema judicial.
