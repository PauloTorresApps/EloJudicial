# 0001. O que é ingerido: os documentos com o toggle ligado

- **Status:** Aceita
- **Data:** 2026-11-23
- **Origem:** escolha do responsável pelo projeto

## Contexto
O módulo de ingestão traz para o EloJudicial os documentos de um processo, que depois serão
analisados por um ou mais agentes de IA. Um processo pode ter milhares de eventos e documentos,
e nem todos interessam à análise.

Antes do módulo, a tabela de eventos do processo já tinha ganhado um toggle por documento
(inclusive os vinculados). Ele vem ligado quando o tipo do documento está na lista do sistema
judicial e desligado nos demais; o usuário pode mudar qualquer um. A lista é por sistema porque
o código usado é o local, da tabela própria de cada tribunal (modelo
`judicial.TipoDocumentoMarcado`, editável no admin).

## Decisão
São ingeridos os documentos com o toggle **ligado** no momento em que o usuário clica em
"Ingerir selecionados". Cada clique cria um lote (`LoteIngestao`) com um registro por documento
(`ArquivoIngerido`).

## Alternativas consideradas
- **Ingerir todos os documentos do processo:** simples, mas baixaria milhares de arquivos sem
  interesse, sobrecarregando o tribunal e o armazenamento.
- **Ingerir só pela lista de tipos, sem escolha do usuário:** tira do usuário o controle sobre
  o que vai para a análise.

## Motivos
O toggle já concentra as duas fontes de critério: a pré-seleção por tipo de documento, ajustada
por tribunal, e a escolha final do usuário.

## Consequências
- O que entra no lote é decidido no navegador. O servidor só aceita ids que estavam na tabela
  exibida ao usuário (ver [0010](0010-metadados-da-tabela-exibida.md)).
- O botão envia os toggles de todas as páginas da tabela, não só os da página visível.
- A qualidade da pré-seleção depende da lista de tipos de cada sistema. Sistemas sem lista
  começam com tudo desligado.
