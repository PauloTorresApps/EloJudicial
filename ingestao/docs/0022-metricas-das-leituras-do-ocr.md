# 0022. Métricas de cada leitura do OCR

- **Status:** Proposta
- **Data:** 2026-11-27
- **Origem:** proposta técnica
- **Complementa:** [0014](0014-medicao-e-calibracao-dos-limiares.md) e [0019](0019-motores-de-ocr-em-dois-niveis.md)

## Contexto
A [0019](0019-motores-de-ocr-em-dois-niveis.md) deixou dois limiares provisórios: a confiança
mínima do Tesseract (0,85, `CONFIANCA_MINIMA_OCR`), abaixo da qual a página vai também ao
RapidOCR, e a qualidade a partir da qual a página sobe para o multimodal. Ela também pediu que as
métricas registrassem, por página, a confiança do Tesseract e a qualidade de cada leitura.

A página só guardava a leitura escolhida (`metodo` e `confianca`). Isso trazia dois problemas:
- **Faltava a confiança do Tesseract quando o RapidOCR vencia.** Era justamente o valor que
  mandou a página para a segunda leitura. Sem ele, não dá para saber se 0,85 está alto ou baixo.
- **As cópias contavam em dobro.** O `estatisticas_extracao` contava de novo as páginas do mesmo
  documento reaproveitadas de outro lote (mesmo SHA-256). No banco local, 31 páginas apareciam
  como 62.

## Decisão
- **`PaginaExtraida.leituras`** (JSON): uma entrada por leitura feita, na ordem (primeiro o
  Tesseract), com `motor`, `confianca`, `qualidade` (`qualidade_do_texto` do texto lido) e
  `segundos`. Não guarda o texto: o que perdeu é descartado e o que venceu já fica em `texto`.
  O reaproveitamento por SHA-256 copia as leituras junto com o texto.
- **`estatisticas_extracao`**, seção do OCR:
  - a **confiança do Tesseract por faixa**, com quantas páginas foram à segunda leitura e em
    quantas o RapidOCR venceu. As faixas se dividem em 0,85, para mostrar o que muda ao mover o
    limiar;
  - a **qualidade do texto do OCR** por motor, para o limiar do multimodal;
  - o **tempo médio por leitura** de cada motor;
  - as páginas lidas antes desta mudança aparecem como "sem leituras". O `reextrair` as lê de
    novo.
- **O `reextrair` lê de novo, sem reaproveitar.** Ao reprocessar o banco local, o reaproveitamento
  por SHA-256 copiou o OCR antigo de um lote para o outro: o arquivo do lote 1 copiava do lote 2,
  ainda não reprocessado, e vice-versa. Nada foi lido de novo, e o `reextrair` existe justamente
  para ler outra vez depois de mudar um limiar ou o motor. O `reconhecer_arquivo` ganha o argumento
  `reaproveitar` (padrão: sim). O `reextrair` o desliga, e as tarefas de continuação
  ([0021](0021-ocr-de-arquivos-grandes-em-blocos.md)) o repassam. As mensagens antigas, só com o
  id, continuam valendo.
- **Páginas repetidas** (mesmo SHA-256 e número de página) contam uma vez só em todas as
  distribuições de páginas. O total ignorado aparece no relatório. A contagem de arquivos não
  muda.

## Alternativas consideradas
- **Colunas fixas** (ex.: `confianca_tesseract`, `qualidade_rapid`): prenderiam o modelo aos
  dois motores de hoje. O multimodal será mais uma leitura da mesma página e cabe na mesma lista.
- **Tabela de leituras**, uma linha por leitura: permitiria consultas em SQL, mas o relatório já
  percorre as páginas em Python, e uma página tem no máximo duas ou três leituras. Uma tabela
  multiplicaria as linhas de um acervo de milhões de páginas sem ganho.
- **Guardar também o texto da leitura que perdeu:** dobraria o espaço das páginas difíceis e
  traria mais conteúdo do documento para um campo de métricas.
- **Simular outro limiar de confiança no relatório, como o `--simular` dos limiares do nativo:**
  só é possível em parte. Uma página acima do limiar atual não tem a leitura do RapidOCR, então não
  se sabe quem venceria. A tabela por faixa mostra o que se sabe, e o resto se mede com
  `reextrair` depois de mudar o limiar.

## Motivos
Com a confiança do Tesseract de toda página lida, a tabela por faixa mostra onde o RapidOCR passa
a vencer. É o dado de que o limiar de 0,85 precisa. Contar cada página uma vez evita que o mesmo
documento ingerido em vários lotes distorça as proporções.

## Consequências
- Migração `0006_leituras_do_ocr`: a coluna nova começa vazia nas páginas já lidas.
- O conjunto das páginas já vistas (SHA-256 e número) fica na memória durante o relatório: são
  algumas dezenas de MB por milhão de páginas.
- `ler_pagina` passa a devolver a leitura escolhida e a lista de métricas.
