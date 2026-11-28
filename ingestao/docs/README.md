# Decisões do módulo de ingestão

Registro das decisões de arquitetura e de produto do módulo `ingestao` (ADR — *Architecture
Decision Records*). Cada decisão tem um arquivo próprio com o contexto, a decisão, as
alternativas consideradas, os motivos e as consequências.

Uma decisão registrada não é apagada nem reescrita. Se mudar, cria-se uma nova decisão que a
substitui, e a antiga passa ao status "Substituída por NNNN".

O fluxo completo da ingestão (download, extração e armazenamento), em diagramas, está em
[fluxo-da-ingestao.md](fluxo-da-ingestao.md).

## Índice

| Nº | Decisão | Status | Data |
|----|---------|--------|------|
| [0001](0001-o-que-e-ingerido.md) | O que é ingerido: os documentos com o toggle ligado | Aceita | 2026-11-23 |
| [0002](0002-execucao-em-segundo-plano-com-celery.md) | Execução em segundo plano com Celery | Aceita | 2026-11-23 |
| [0003](0003-redis-separado-para-a-fila.md) | Redis separado para a fila, com persistência | Aceita | 2026-11-23 |
| [0004](0004-estado-no-postgresql-mensagens-so-com-ids.md) | Estado no PostgreSQL; mensagens da fila só com ids | Aceita | 2026-11-23 |
| [0005](0005-acesso-ao-tribunal-pelo-worker.md) | Acesso ao tribunal pelo worker sem persistir a senha | Aceita | 2026-11-23 |
| [0006](0006-tarefas-longas-e-idempotencia.md) | Configuração para tarefas longas e idempotência | Aceita (tarefa por arquivo substituída por 0017) | 2026-11-23 |
| [0007](0007-armazenamento-em-volume-em-disco.md) | Originais num volume em disco | Aceita | 2026-11-23 |
| [0008](0008-extracao-de-texto-sem-ocr.md) | Extração de texto de PDF e HTML, sem OCR | Aceita (complementada por 0013) | 2026-11-23 |
| [0009](0009-arquivos-pertencem-ao-usuario.md) | Lotes e arquivos pertencem ao usuário | Aceita | 2026-11-23 |
| [0010](0010-metadados-da-tabela-exibida.md) | Metadados dos documentos vêm da tabela exibida | Aceita | 2026-11-23 |
| [0011](0011-acompanhamento-do-lote-na-tela.md) | Acompanhamento do lote na tela | Aceita | 2026-11-23 |
| [0012](0012-versoes-das-dependencias.md) | Versões das dependências novas | Aceita | 2026-11-23 |
| [0013](0013-deteccao-de-texto-por-pagina.md) | Detecção de texto por página | Aceita | 2026-11-24 |
| [0014](0014-medicao-e-calibracao-dos-limiares.md) | Medição e calibração dos limiares | Aceita | 2026-11-24 |
| [0015](0015-revisao-de-simplificacao.md) | Revisão de simplificação do módulo | Aceita | 2026-11-24 |
| [0016](0016-uma-requisicao-por-vez-ao-tribunal.md) | Uma requisição por vez a cada tribunal, com intervalo mínimo | Aceita | 2026-11-25 |
| [0017](0017-download-do-lote-numa-unica-requisicao.md) | Download do lote numa única requisição ao tribunal | Aceita | 2026-11-25 |
| [0018](0018-ocr-em-modulo-e-worker-proprios.md) | OCR em módulo e worker próprios | Aceita (complementada por 0019 e 0020) | 2026-11-26 |
| [0019](0019-motores-de-ocr-em-dois-niveis.md) | Motores de OCR em dois níveis: Tesseract e RapidOCR | Aceita | 2026-11-26 |
| [0020](0020-situacao-reconhecendo-durante-o-ocr.md) | Situação "Reconhecendo texto" durante o OCR | Proposta | 2026-11-27 |

## Próximos passos

Situação em 2026-11-24: passos 1 (detecção por página, [0013](0013-deteccao-de-texto-por-pagina.md))
e 2 (medição, [0014](0014-medicao-e-calibracao-dos-limiares.md)) da cascata de extração prontos.
O que vem a seguir, em ordem:

1. **Coletar dados reais** (responsável pelo projeto):
   - ingerir processos reais de mais de um sistema (eproc, PJe, e-SAJ), incluindo processos com
     documentos escaneados;
   - rodar `docker compose exec web python manage.py estatisticas_extracao` e guardar a saída
     (só contagens, sem dados de processo);
   - conferir por amostragem páginas classificadas como "para OCR" e como "com texto".
2. **Calibrar os limiares:** simular valores com `--simular`, ajustar `Limiares` em
   `extracao.py`, registrar a mudança num novo ADR com os números que a justificaram e rodar
   `reextrair --todos`.
3. **Decisões de produto antes do OCR e do multimodal** (não dependem de código):
   - provedor do modelo multimodal, condições contratuais e LGPD;
   - se documentos com nível de sigilo acima de zero podem ser enviados a ele;
   - teto de custo por lote.
4. **Passo 3 da cascata, camada de OCR local** (divisão em módulo `ocr`, orquestração no
   `ingestao` e `worker-ocr` proposta na [0018](0018-ocr-em-modulo-e-worker-proprios.md)):
   - ~~comparar os motores numa amostra real~~: feito, Tesseract e, nas páginas de baixa
     confiança, RapidOCR, propostos na [0019](0019-motores-de-ocr-em-dois-niveis.md);
   - ~~fila e worker próprios (`ocr`), método de página e confiança por página~~: feito em
     2026-11-26 (módulo `ocr`, tarefa `ingestao.reconhecer_arquivo`, serviço `worker-ocr`,
     `requirements-ocr.txt`); validado nos lotes 1 e 2 (31 páginas: 28 pelo Tesseract, 3 pelo
     RapidOCR, todos os arquivos "Pronto");
   - ~~situação do arquivo durante o OCR~~: feito em 2026-11-27, "Reconhecendo texto"
     ([0020](0020-situacao-reconhecendo-durante-o-ocr.md));
   - dividir o OCR de arquivos enormes em tarefas por faixa de páginas (um PDF de milhares de
     páginas pode passar do `visibility_timeout` de 2 h e ser entregue de novo);
   - pré-processamento de fotos e xerox (endireitar, binarizar, tirar ruído);
   - calibrar o limiar de confiança do Tesseract (0,85, `CONFIANCA_MINIMA_OCR`) e o de qualidade a
     partir do qual a página sobe para o multimodal, com mais processos escaneados.
5. **Passo 3 da cascata, camada multimodal:** só para as páginas abaixo do limiar de confiança,
   respeitando as decisões do item 3.
6. **Pendências menores do módulo** (podem entrar a qualquer momento):
   - apagar a chave de acesso ao tribunal quando o lote termina
     ([0015](0015-revisao-de-simplificacao.md));
   - política de retenção e tela para apagar lotes;
   - paginação e contagem de páginas guardada, se lotes grandes ficarem lentos;
   - entrega dos originais em streaming;
   - confirmar nos outros sistemas (PJe, e-SAJ, Projudi) e no MNI 2.2 que o tribunal devolve
     todos os documentos na mesma resposta; no eproc do TJTO (MNI 3.0) já confirmado
     ([0017](0017-download-do-lote-numa-unica-requisicao.md));
   - extração em tarefas separadas do download, se lotes grandes ficarem lentos
     ([0017](0017-download-do-lote-numa-unica-requisicao.md)).
7. **Agentes de IA:** fila `ia` separada, orquestração (`chain`/`chord`) e limite de taxa por
   provedor, consumindo o texto das páginas já extraídas
   ([0002](0002-execucao-em-segundo-plano-com-celery.md)).

Os detalhes de cada ponto ainda sem decisão estão na seção abaixo.

## Decisões em aberto

Pontos levantados que ainda precisam de decisão:

- **Retenção dos arquivos:** hoje os originais e o texto ficam guardados até o lote ser apagado,
  e não há tela para apagar lotes. Falta definir por quanto tempo guardar e quem pode apagar.
- **Chave de acesso de lotes concluídos:** ao sair do sistema, só as chaves de lotes em
  andamento são apagadas; as de lotes concluídos expiram com o `MNI_SENHA_TTL`. Sugestão: apagar
  a chave quando o lote termina (ver [0015](0015-revisao-de-simplificacao.md)).
- **Desempenho de lotes grandes:** paginação das listas e contagem de páginas guardada no
  arquivo, se a tela do lote ficar lenta (ver [0015](0015-revisao-de-simplificacao.md)).
- **Carga no tribunal:** decidida na [0016](0016-uma-requisicao-por-vez-ao-tribunal.md) (uma
  requisição por vez por tribunal, intervalo mínimo de 2 s) e na
  [0017](0017-download-do-lote-numa-unica-requisicao.md) (um envelope por lote). Fica em aberto só
  um intervalo próprio por tribunal, se algum exigir mais folga que o `MNI_INTERVALO`.
- **Entrega de originais grandes:** o download pela tela lê o arquivo inteiro na memória (até
  100 MB). Se pesar, passar a entregar em streaming.
- **Cascata de extração, passo 3 (depende das medições da [0014](0014-medicao-e-calibracao-dos-limiares.md)):**
  - **Motor de OCR:** proposta na [0019](0019-motores-de-ocr-em-dois-niveis.md) (Tesseract e,
    abaixo de 0,85 de confiança, RapidOCR; PaddleOCR descartado). Falta o pré-processamento de
    fotos e xerox (endireitar, binarizar, tirar ruído) e confirmar os limiares com mais processos.
    A fila e o worker próprios (`ocr`) estão propostos na [0018](0018-ocr-em-modulo-e-worker-proprios.md).
  - **Página do PDF em imagem:** pypdfium2, pdftoppm ou a imagem embutida, e a resolução.
  - **Limiar de qualidade** a partir do qual a página sobe para o modelo multimodal, depois das
    duas leituras ([0019](0019-motores-de-ocr-em-dois-niveis.md)).
  - **Modelo multimodal:** provedor, condições contratuais e LGPD, se documentos com nível de
    sigilo acima de zero podem ser enviados, e teto de custo por lote.
- **Calibração dos limiares** da [0013](0013-deteccao-de-texto-por-pagina.md), com ingestões reais.
- **Agentes de IA:** fila `ia` separada, orquestração (`chain`/`chord`) e limite de taxa por
  provedor (ver [0002](0002-execucao-em-segundo-plano-com-celery.md)).

## Como registrar uma nova decisão

1. Copie o modelo abaixo para `NNNN-titulo-curto.md`, com o próximo número.
2. Preencha todas as seções. Em "Alternativas consideradas", registre também as descartadas e o
   porquê: é o que mais se perde com o tempo.
3. Acrescente a linha no índice acima. Se a decisão substitui outra, atualize o status da antiga.

```markdown
# NNNN. Título da decisão

- **Status:** Proposta | Aceita | Substituída por NNNN
- **Data:** AAAA-MM-DD
- **Origem:** escolha do responsável pelo projeto | proposta técnica aceita

## Contexto
O problema ou a necessidade, e as restrições que pesam sobre ele.

## Decisão
O que foi decidido, de forma direta.

## Alternativas consideradas
- **Alternativa A:** prós, contras e por que não foi escolhida.

## Motivos
Por que esta opção e não as outras.

## Consequências
O que passa a valer, o que fica mais fácil, o que fica mais difícil e o que precisa de cuidado.
```
