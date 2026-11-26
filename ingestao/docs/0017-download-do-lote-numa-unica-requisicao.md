# 0017. Download do lote numa única requisição ao tribunal

- **Status:** Aceita
- **Data:** 2026-11-25
- **Origem:** escolha do responsável pelo projeto
- **Substitui em parte:** [0006](0006-tarefas-longas-e-idempotencia.md) (tarefa por arquivo)

## Contexto
Cada arquivo de um lote era uma tarefa (`ingerir_arquivo`) e uma requisição ao tribunal. Um lote
com 50 documentos fazia 50 requisições, até 4 ao mesmo tempo, cada uma com a autenticação
repetida no envelope. Isso contraria a regra da
[0016](0016-uma-requisicao-por-vez-ao-tribunal.md) e expõe o IP ao bloqueio.

Os esquemas do MNI permitem pedir vários documentos de uma vez:
- **MNI 3.0:** `idDocumento` em `requisicaoConsultarDocumentosProcesso` tem
  `maxOccurs="unbounded"` (conferido no XSD do eproc do TJTO).
- **MNI 2.2.2:** `documento` em `consultarProcesso` tem `maxOccurs="unbounded"` ("lista de
  identificadores dos documentos que se pretende recuperar").

## Decisão
**Uma tarefa por lote e um envelope com todos os ids:**
- `enfileirar(lote, ids)` põe na fila uma única tarefa, `ingerir_lote(lote_id, arquivos_ids)`.
- A tarefa monta **um envelope** com um `<idDocumento>` (3.0) ou `<documento>` (2.2) por arquivo,
  na ordem da tabela de eventos, com `mni.consultar_documentos(...)`.
- Cada conteúdo da resposta é associado ao arquivo **pelo id** (`ResultadoDocumentos.por_id`). A
  tolerância antiga ("se o id não bater, usar o primeiro documento da resposta") vale só quando
  foi pedido um único documento. Com vários, ela gravaria o conteúdo de um documento no lugar de
  outro. Documento que não vem na resposta fica com erro sozinho.
- Cada original é guardado e tem o texto extraído na sequência. O conteúdo sai da memória assim
  que é guardado.

**Quando há mais de uma requisição** (sempre espaçadas pelo limitador da
[0016](0016-uma-requisicao-por-vez-ao-tribunal.md)):
- **Resposta acima de 100 MB** (`TAMANHO_MAXIMO_DOCUMENTO`): o grupo é dividido ao meio e cada
  metade é pedida de novo, quantas vezes for preciso. Um documento sozinho acima do limite fica
  com erro.
- **Falha de comunicação:** até 3 novas tentativas (30 s, 60 s, 120 s). Cada uma leva só os
  arquivos que faltaram, de novo num único envelope.
- **Senha recusada:** o lote para na hora, sem pedir os grupos restantes, e passa a "Aguardando
  senha".

**Idempotência** (mantida da [0006](0006-tarefas-longas-e-idempotencia.md), agora por lote): a
tarefa trava e pega, numa transação, só os arquivos do lote ainda "na fila" ou "baixando". Uma
mensagem entregue duas vezes não baixa de novo o que já terminou.

**Compatibilidade:** `ingerir_arquivo` continua registrada só para repassar a `ingerir_lote` as
mensagens antigas que estavam na fila. Pode ser removida quando a fila esvaziar.

**Fora da ingestão:** abrir um documento pela tabela de eventos continua sendo uma requisição
por clique, com um id, porque é uma ação pontual do usuário.

## Alternativas consideradas
- **Manter uma requisição por arquivo, só serializada e espaçada:** cumpre a regra, mas um lote
  de 50 documentos levaria mais de 100 s só de intervalos e repetiria a autenticação 50 vezes.
- **Grupos de tamanho fixo (ex.: 10 documentos por envelope):** limita o tamanho da resposta,
  mas multiplica as requisições sem necessidade na maioria dos lotes. O responsável pelo projeto
  pediu todos os documentos no mesmo envelope. A divisão fica só como reação ao limite de
  tamanho.
- **Grupos dimensionados pelo tamanho conhecido dos documentos:** o MNI nem sempre informa o
  tamanho na consulta do processo. Fica para depois, se a divisão por tamanho acontecer muito.
- **Download numa tarefa e extração em tarefas separadas por arquivo:** devolveria o paralelismo
  da extração (que não toca o tribunal) sem aumentar as requisições. É mais uma etapa na fila.
  Fica para quando lotes grandes ficarem lentos.

## Motivos
É a forma de baixar um lote com o menor número possível de requisições (normalmente uma), usando
um recurso previsto nas duas versões do MNI, sem perder a idempotência nem o tratamento de falhas
por arquivo.

## Consequências
- Um lote comum (abaixo de 100 MB no total) gera **exatamente uma requisição** ao tribunal.
- A resposta é maior e leva mais tempo. O `TIMEOUT` de 60 s vale por operação de leitura do
  socket, não para o download inteiro, então respostas grandes que continuam chegando não
  estouram o prazo.
- Uma falha de comunicação ou um conteúdo corrompido (base64 inválido) derruba o grupo inteiro,
  que é tentado de novo junto. Antes falhava só o arquivo.
- A extração de texto dos arquivos de um mesmo lote passou a ser sequencial (antes até 4 em
  paralelo). Lotes diferentes continuam em paralelo no worker.
- Cada divisão por tamanho baixa e descarta uma resposta de até 100 MB antes de pedir as metades.
- **A confirmar em produção:** ainda não foi ingerido um lote real com vários documentos. Falta
  conferir se cada tribunal devolve todos os documentos pedidos na mesma resposta (os testes
  simulam esse comportamento).
