# 0019. Motores de OCR em dois níveis: Tesseract e RapidOCR

- **Status:** Aceita
- **Data:** 2026-11-26
- **Origem:** proposta técnica aceita
- **Complementa:** [0018](0018-ocr-em-modulo-e-worker-proprios.md) (escolha do motor, que lá ficou em aberto)

## Contexto
A [0018](0018-ocr-em-modulo-e-worker-proprios.md) separou o motor de OCR (módulo `ocr`) da
orquestração e deixou a escolha do motor para depois de uma comparação com páginas reais.

**Como foi a comparação** (2026-11-26):
- **Amostra:** as 31 páginas "sem texto" dos 11 PDFs do lote 1 (eproc do TJTO, 1º Grau). As
  imagens embutidas estavam a 148 dpi (3 páginas, um xerox com carimbos e manuscritos), 200 dpi
  (a maioria) e 300 dpi.
- **Condições:** páginas renderizadas a 300 dpi com pypdfium2, sem pré-processamento, cada motor
  com uma única thread (como no worker), tempo de inicialização e primeira página fora da medição.
- **Medidas por página:** tempo, confiança média ponderada pelo tamanho do texto, caracteres e a
  nota de `qualidade_do_texto` (a mesma da [0013](0013-deteccao-de-texto-por-pagina.md)).
- Feita num container descartável com Python 3.13, que lia o volume só para leitura, sem gravar
  nada no banco.

**Resultado:**

| | Tesseract 5.5.0 (`por`) | RapidOCR 3.10 (PP-OCRv5 mobile, latino, ONNX) | PaddleOCR 3.7 (`lang='pt'`) |
|---|---|---|---|
| Tempo por página (mediana / máximo) | **1,4 s** / 2,1 s | 3,9 s / 5,4 s | 62 s / 74 s |
| Tempo total, 31 páginas | **44 s** | 116 s | 1.904 s |
| Qualidade, mediana | 0,96 | 0,96 | 0,96 |
| Qualidade no xerox de 148 dpi (3 páginas) | 0,60 / 0,84 / 0,85 | 0,85 / 0,87 / 0,92 | **0,94 / 0,95 / 0,96** |
| Confiança, páginas do xerox | 0,51 a 0,76 | 0,95 a 0,96 | 0,96 |
| Confiança, demais páginas | 0,90 a 0,95 | 0,96 a 0,99 | 0,98 a 0,99 |
| Python 3.14 | sim | sim (há pacote do `onnxruntime` para o 3.14) | não (o PaddlePaddle 3.3.1 só tem pacote até o 3.13) |
| Imagem Docker | menor | média | 2,9 GB |

Observações:
- Nas 28 páginas limpas os três empatam: diferenças de poucos caracteres, cada um com erros
  próprios (ex.: o Tesseract leu "Pedro AfonsoiTO"; RapidOCR e PaddleOCR tiraram o acento de
  "Polícia").
- Só a confiança do Tesseract separa as páginas ruins das boas. A do RapidOCR fica alta em
  todas, porque por padrão ele descarta as linhas com nota abaixo de 0,5 antes da média. A do
  PaddleOCR também ficou alta em todas.
- O PaddleOCR rodou sem a aceleração oneDNN da CPU, que falha nesta versão do PaddlePaddle com
  `NotImplementedError (ConvertPirAttribute2RuntimeAttribute)`. Com ela seria mais rápido, mas
  continuaria muito acima dos outros dois.
- Havia documentos repetidos no lote (dois pares com o mesmo conteúdo), que geraram o mesmo
  texto duas vezes.

## Decisão
**OCR em dois níveis, por página:**
1. **Tesseract** (`por`) em todas as páginas que precisam de OCR.
2. Se a **confiança do Tesseract** ficar abaixo do limiar, uma segunda leitura com o **RapidOCR**
   (PP-OCRv5 mobile, reconhecimento latino). Fica gravado o texto da leitura com melhor
   `qualidade_do_texto`, com o `metodo` correspondente.
3. Se a página continuar abaixo do limiar de qualidade depois das duas leituras, ela fica marcada
   para a camada multimodal (decisão própria, ainda em aberto).

**Limiares iniciais, provisórios:**
- Confiança do Tesseract abaixo de **0,85** dispara o RapidOCR. Na amostra, as páginas ruins
  ficaram entre 0,51 e 0,76 e as boas, acima de 0,90.
- A decisão de enviar ao multimodal usa a `qualidade_do_texto`, não a confiança do RapidOCR, que
  não separa as páginas. O valor fica para a calibração.

**Outros pontos:**
- **Idioma:** só `por` no Tesseract. Documentos em outras línguas são raros e o reconhecimento
  latino do RapidOCR cobre os acentos.
- **Documentos repetidos:** antes do OCR, um arquivo com o mesmo SHA-256 de outro já reconhecido
  do mesmo usuário reaproveita o texto, sem rodar o OCR de novo.
- **PaddleOCR:** descartado para o worker.

**Ainda em aberto:**
- a resolução da imagem (300 dpi fixo ou a resolução da imagem embutida, já que a maioria das
  imagens tem 200 dpi e a renderização a 300 dpi só aumenta a imagem);
- o pré-processamento (endireitar, binarizar, tirar ruído), que não foi testado;
- o modo de chamada do Tesseract: `pytesseract`, que chama o programa a cada página e foi o usado
  na comparação, ou `tesserocr`, que usa a biblioteca direto e tem pacote para o 3.14;
- confirmar o RapidOCR no Python 3.14 (na comparação ele rodou no 3.13).

## Alternativas consideradas
- **Só o Tesseract:** o mais simples e o mais rápido, mas lê mal o xerox de baixa resolução
  (qualidade 0,60 na pior página) e mandaria mais páginas ao multimodal, que é pago e tem
  restrições de sigilo.
- **Só o RapidOCR:** melhor no xerox, mas 2,7 vezes mais lento nas páginas limpas, que são a
  maioria, e sem uma confiança que aponte as páginas ruins.
- **PaddleOCR (sozinho ou como segundo nível):** a melhor qualidade no xerox, mas cerca de 1
  minuto por página numa thread, sem pacote para o Python 3.14, com a aceleração de CPU quebrada
  nesta versão e uma imagem de 2,9 GB. O RapidOCR usa modelos da mesma família (PP-OCR) a uma
  fração do custo.
- **Modelos PP-OCRv6 no RapidOCR** (disponíveis na 3.10): não testados. Ficam para uma nova
  comparação, se o PP-OCRv5 não bastar.

## Motivos
Nas páginas limpas o Tesseract dá o mesmo resultado que os outros, em menos tempo, e é o único
que indica com a confiança quando errou. O segundo nível só gasta tempo onde o primeiro foi mal,
e é justamente onde o RapidOCR mostrou ganho. Os dois rodam no Python 3.14 do projeto.

## Consequências
- A imagem do `worker-ocr` leva o Tesseract com o idioma português (pacotes do sistema),
  `pypdfium2`, o RapidOCR e o `onnxruntime`. Os modelos do RapidOCR devem vir na imagem, porque
  hoje ele os baixa da internet na primeira execução.
- `PaginaExtraida.Metodo` passa a distinguir as duas leituras (ex.: `ocr_tesseract` e
  `ocr_rapid`), para medir quanto cada nível resolve.
- O custo por página fica em cerca de 1,4 s nas páginas boas e cerca de 5,5 s nas que precisam
  do segundo nível, numa thread. O paralelismo do worker multiplica a vazão.
- A amostra é pequena: 31 páginas de um processo, só 3 realmente difíceis. Os limiares precisam
  ser revistos com mais processos de documentos escaneados, de mais de um sistema.
- As métricas de [0014](0014-medicao-e-calibracao-dos-limiares.md) devem registrar por página
  a confiança do Tesseract e a qualidade de cada leitura, para calibrar os dois limiares.
