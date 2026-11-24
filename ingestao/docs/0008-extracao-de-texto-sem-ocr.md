# 0008. Extração de texto de PDF e HTML, sem OCR

- **Status:** Aceita (complementada por [0013](0013-deteccao-de-texto-por-pagina.md): a decisão
  passou a ser por página, e as páginas que precisam de OCR ficam identificadas)
- **Data:** 2026-11-23
- **Origem:** escolha do responsável pelo projeto

## Contexto
Os agentes de IA vão trabalhar sobre o texto dos documentos. A dúvida era se a ingestão já deve
extrair o texto ou só guardar o original, e se deve incluir OCR para PDFs digitalizados (que são
só imagem).

## Decisão
A ingestão guarda o original **e** extrai o texto, sem OCR (`ingestao/extracao.py`):
- **PDF:** a camada de texto, com o pypdf. PDF sem texto (digitalizado) fica com a situação
  "Sem texto" e a mensagem de que precisa de OCR.
- **HTML:** só o texto visível, sem scripts, estilos e título, com os espaços do código-fonte
  colapsados e parágrafos separados por uma linha em branco.
- **Texto puro:** decodificado (codificação informada pelo tribunal, depois UTF-8 e, por fim,
  cp1252, comum em documentos antigos).
- **Outros tipos** (imagens, áudio, vídeo): guardados, com a situação "Sem texto".

O texto fica em `ArquivoIngerido.texto`, no PostgreSQL, junto com o número de páginas do PDF.

## Alternativas consideradas
- **Incluindo OCR (Tesseract na imagem Docker):** cobriria os digitalizados, mas é bem mais
  pesado e lento, e aumentaria a imagem.
- **Só o original:** deixaria a extração para a etapa dos agentes, que teria de repeti-la a cada
  análise.

## Motivos
Extrair uma vez, na ingestão, deixa o texto pronto para qualquer agente. O OCR fica para depois,
quando se souber quantos documentos digitalizados aparecem na prática; os que precisam dele já
ficam identificados.

## Consequências
- Um PDF protegido ou corrompido não derruba a tarefa: o original é guardado e o arquivo fica
  "Sem texto", com a mensagem.
- O texto em HTML e texto puro usa só a biblioteca padrão do Python; o PDF depende do pypdf
  (ver [0012](0012-versoes-das-dependencias.md)).
- Para adicionar OCR depois, basta reprocessar os arquivos "Sem texto" de tipo PDF.
