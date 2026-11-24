# 0013. Detecção de texto por página

- **Status:** Aceita (complementa a [0008](0008-extracao-de-texto-sem-ocr.md))
- **Data:** 2026-11-24
- **Origem:** proposta técnica aceita (passo 1 da cascata de extração)

## Contexto
Os documentos ingeridos misturam PDFs com texto nativo, xerox escaneados e fotos de celular. A
direção adotada é uma extração em cascata: texto nativo (custo zero), depois OCR local e, nos
casos difíceis, um modelo multimodal. Cada camada só deve tratar o que a anterior não resolveu.

A primeira versão (decisão 0008) decidia "tem texto ou não" para o **documento inteiro**. Peças
judiciais costumam ser mistas, por exemplo uma petição digitada com anexos escaneados no mesmo
PDF. Nesse caso o arquivo ficava "Pronto" com o texto das páginas digitadas, e as escaneadas se
perdiam sem aviso. Além disso, no eproc a página escaneada costuma trazer o carimbo "documento
assinado eletronicamente" em texto nativo, o que a faria parecer uma página com texto.

## Decisão
A extração passa a ser **por página** (`ingestao/extracao.py`), e cada página é classificada:

| Situação | Regra | Precisa de OCR |
|----------|-------|----------------|
| Com texto | texto suficiente e de boa qualidade | não |
| Em branco | menos de 20 caracteres e nenhuma imagem grande | não |
| Digitalizada (sem texto) | imagem grande e menos de 20 caracteres; **ou** imagem grande e menos de 300 caracteres (página escaneada com carimbo) | sim |
| Texto de baixa qualidade | qualidade do texto abaixo de 0,6 | sim |

- **Imagem grande:** a maior imagem da página com pelo menos 500 mil pixels (cerca de 700×700).
  Logotipos e brasões ficam bem abaixo disso; páginas escaneadas, bem acima (uma A4 a 150 dpi
  tem cerca de 2,2 milhões). O tamanho é lido do dicionário da imagem (`/Width`, `/Height`), sem
  decodificá-la, inclusive dentro de XObjects de formulário.
- **Qualidade do texto:** proporção de "palavras plausíveis" (letras com acentos, números, datas,
  valores, referências como "art. 5º"), descontada a proporção de caracteres estranhos
  (substituição, uso privado, controle). Pega OCR antigo ruim embutido no PDF e codificação
  quebrada.
- **Imagens avulsas** (JPEG, PNG, TIFF…) viram uma página "Digitalizada". HTML e texto puro viram
  uma página classificada pelas mesmas regras.
- **Situação do arquivo:**
  - "Pronto" quando nenhuma página precisa de OCR;
  - "Texto parcial" (situação nova) quando há páginas com texto e páginas que precisam de OCR,
    com a mensagem "N de M páginas precisam de OCR (páginas 2, 5–7)";
  - "Sem texto" quando nenhuma página tem texto aproveitável: em branco, ou "Sem texto
    aproveitável: precisa de OCR (…)".
  - Só páginas "Com texto" contam como texto aproveitável: o carimbo de uma página escaneada não
    faz o arquivo parecer "parcial".
- **Modelo:** cada página vira um `PaginaExtraida` com o texto, a situação, o método (por ora só
  "nativo") e as métricas (caracteres, qualidade, maior imagem). O texto do arquivo
  (`ArquivoIngerido.texto`) passou a ser montado a partir das páginas, para não guardar o mesmo
  texto duas vezes.
- **Limiares** numa estrutura própria (`Limiares`), com a regra de classificação como função só
  das métricas (`situacao_da_pagina`). Isso permite simular outros valores sobre as métricas
  guardadas (ver [0014](0014-medicao-e-calibracao-dos-limiares.md)).

## Alternativas consideradas
- **Manter a decisão por documento:** perde em silêncio as páginas escaneadas de PDFs mistos.
- **Decidir só pela quantidade de texto, sem olhar as imagens:** a página escaneada com carimbo
  passaria por página com texto; e uma folha em branco iria para o OCR sem necessidade.
- **Medir a área ocupada pela imagem na página:** mais preciso que o tamanho em pixels, mas
  exige interpretar o conteúdo da página (matrizes de transformação). O tamanho em pixels resolve
  o caso comum com custo quase nulo.
- **Renderizar a página e analisar a imagem:** caro, e precisaria de outra dependência.
- **PyMuPDF em vez de pypdf:** mais rápido e com melhor extração, mas com licença AGPL, que
  obriga a disponibilizar o código de um sistema oferecido pela rede (ou exige licença
  comercial). Fica o pypdf (BSD), a reavaliar se a qualidade da extração nativa se mostrar
  insuficiente.

## Motivos
Decidir por página é o que torna a cascata eficiente: só as páginas ruins sobem de camada, o
que reduz o custo do OCR e, principalmente, o do modelo multimodal. E nada se perde sem aviso.

## Consequências
- Os limiares são **provisórios**, escolhidos sem dados reais. Serão calibrados com ingestões
  reais (decisão [0014](0014-medicao-e-calibracao-dos-limiares.md)).
- Erros prováveis até a calibração:
  - certidões curtas com uma imagem grande de cabeçalho podem ir para OCR sem necessidade
    (custo, não perda);
  - páginas escaneadas com muito texto nativo, acima de 300 caracteres, passariam como "com
    texto".
- O banco cresce com uma linha por página. O texto não é duplicado.
- A migração inicial do módulo foi refeita em vez de acrescentada, porque o módulo ainda não
  tinha sido publicado e não havia dados.
