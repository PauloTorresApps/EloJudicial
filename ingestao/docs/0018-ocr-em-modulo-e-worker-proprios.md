# 0018. OCR em módulo e worker próprios

- **Status:** Aceita
- **Data:** 2026-11-26
- **Origem:** proposta técnica aceita
- **Complementa:** [0008](0008-extracao-de-texto-sem-ocr.md) e [0013](0013-deteccao-de-texto-por-pagina.md)

## Contexto
A detecção por página ([0013](0013-deteccao-de-texto-por-pagina.md)) já marca as páginas que
precisam de OCR (`sem_texto` e `texto_ruim`, em `PaginaExtraida.PRECISAM_OCR`). No primeiro lote
real ([0017](0017-download-do-lote-numa-unica-requisicao.md)), 11 dos 27 documentos ficaram sem
texto aproveitável. Falta a camada que lê essas páginas como imagem.

Restrições:
- OCR gasta muita CPU e memória e pode levar segundos por página. Não pode atrasar o download nem
  disputar o worker da fila `ingestao`.
- O motor (Tesseract ou PaddleOCR) ainda não foi escolhido. A troca não pode exigir mexer na
  orquestração nem nos modelos.
- O OCR trabalha sobre o original já guardado no volume ([0007](0007-armazenamento-em-volume-em-disco.md)).
  Ele **não chama o tribunal**, então a regra de uma requisição por vez
  ([0016](0016-uma-requisicao-por-vez-ao-tribunal.md)) não se aplica a ele.

## Decisão
**1. Módulo `ocr`, sem modelos Django.** Uma biblioteca pura, que não conhece lote, arquivo nem
banco:
- entrada: a imagem de uma página (e, opcionalmente, o idioma);
- saída: o texto e a confiança (0 a 1), mais o tempo gasto;
- inclui o pré-processamento (endireitar, binarizar, tirar ruído) e a escolha do motor atrás de
  uma interface única, para que Tesseract e PaddleOCR possam ser comparados e trocados.

**2. Orquestração no `ingestao`:**
- Ao terminar a extração de um arquivo com páginas em `PRECISAM_OCR`, o `ingerir_lote` põe na
  fila `ocr` uma tarefa por arquivo, `reconhecer_arquivo(arquivo_id)`, depois do commit e só com o
  id ([0004](0004-estado-no-postgresql-mensagens-so-com-ids.md)).
- A tarefa lê o original do volume, transforma cada página selecionada em imagem, chama o módulo
  `ocr` e grava na própria `PaginaExtraida`: o texto, `metodo = ocr` e a nova coluna `confianca`.
- A situação da página é reclassificada com o texto do OCR. A página que fica abaixo do limiar de
  confiança continua em `PRECISAM_OCR` para a camada multimodal.
- Ao final, a situação do arquivo (`pronto`, `parcial`, `sem_texto`) é recalculada.
- **Idempotência:** a tarefa só processa páginas ainda com `metodo = nativo` em `PRECISAM_OCR`.
  Uma mensagem repetida não refaz o que já foi reconhecido.

**3. Worker e imagem Docker próprios (`worker-ocr`):**
- Mesmo código do projeto, numa imagem (ou *target* do `Dockerfile`) que acrescenta o motor de
  OCR e o idioma português. A imagem do `web` e do `worker` continua leve.
- Consome só a fila `ocr` (`CELERY_TASK_ROUTES` ganha a rota da tarefa nova).
- Pode rodar em paralelo, com concorrência ajustada ao número de CPUs e o motor limitado a uma
  *thread* por processo, para não disputar CPU entre si.

**Ficam para decisões seguintes** (registradas quando houver dados):
- o motor, depois de comparar Tesseract e PaddleOCR nas páginas "sem texto" do lote 1 (tempo,
  confiança, qualidade, tamanho da imagem, suporte ao Python 3.14);
- como transformar a página do PDF em imagem (pypdfium2, pdftoppm ou a imagem embutida) e a
  resolução;
- o limiar de confiança a partir do qual a página sobe para o multimodal;
- se o arquivo ganha uma situação própria durante o OCR (ex.: "Reconhecendo texto") para a tela
  do lote ([0011](0011-acompanhamento-do-lote-na-tela.md)).

## Alternativas consideradas
- **OCR dentro do `ingerir_lote`, no mesmo worker:** mais simples, mas o lote só terminaria depois
  do OCR de todas as páginas, e a imagem do `web`/`worker` carregaria o motor sem precisar.
- **OCR dentro do `ingestao/extracao.py`:** misturaria a extração nativa (rápida, sem dependências
  pesadas) com o motor de OCR e prenderia a orquestração a um motor específico.
- **App Django `ocr` com modelos próprios (ex.: tabela de resultados de OCR):** duplicaria o que a
  `PaginaExtraida` já guarda. Uma página tem um texto final e um método; a confiança cabe numa
  coluna.
- **Serviço de OCR separado, com API HTTP:** isolaria mais, mas acrescenta um serviço, uma API e
  tratamento de falhas de rede sem ganho neste tamanho de projeto. A fila do Celery já dá o
  isolamento e a retomada.
- **OCR em nuvem:** fica fora desta camada. O envio de páginas para fora é decisão da camada
  multimodal (provedor, LGPD, sigilo e custo).

## Motivos
Separar o motor (módulo `ocr`) da orquestração (`ingestao`) permite comparar e trocar o motor sem
tocar em tarefas ou modelos. A fila e o worker próprios isolam a carga de CPU e deixam o download
e a extração nativa com o mesmo tempo de hoje. Como o OCR não fala com o tribunal, pode ter
paralelismo sem risco de bloqueio do IP.

## Consequências
- Um lote passa a ter duas fases: o arquivo fica "Pronto"/"Parcial"/"Sem texto" depois do
  download e pode mudar de situação quando o OCR terminar.
- Migração nova: `PaginaExtraida.Metodo` ganha `ocr` e a tabela ganha `confianca` (nula para o
  texto nativo).
- Mais um serviço no `compose.yaml` e uma imagem maior para ele. A imagem do `web` não muda.
- O `reextrair` regrava as páginas a partir do original e apagaria o resultado do OCR. Ele terá de
  preservar as páginas com `metodo = ocr` ou pôr o arquivo de novo na fila `ocr`.
- As métricas de [0014](0014-medicao-e-calibracao-dos-limiares.md) (`estatisticas_extracao`)
  devem passar a separar texto nativo e texto de OCR, com a distribuição da confiança, para
  calibrar o limiar do multimodal.
