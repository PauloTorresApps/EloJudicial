# 0020. Situação "Reconhecendo texto" durante o OCR

- **Status:** Proposta
- **Data:** 2026-11-27
- **Origem:** proposta técnica
- **Complementa:** [0018](0018-ocr-em-modulo-e-worker-proprios.md) (situação do arquivo durante o OCR,
  que lá ficou em aberto) e [0011](0011-acompanhamento-do-lote-na-tela.md)

## Contexto
Com o OCR ([0018](0018-ocr-em-modulo-e-worker-proprios.md), [0019](0019-motores-de-ocr-em-dois-niveis.md)),
um lote tem duas fases: o download, na fila `ingestao`, e o reconhecimento das páginas
digitalizadas, na fila `ocr`. Até aqui o arquivo saía do download como "Texto parcial" ou "Sem
texto, precisa de OCR" e só mudava quando o OCR terminava. Com isso:
- a tela do lote dava o lote por concluído e parava de consultar o andamento enquanto o OCR ainda
  rodava;
- o usuário via "precisa de OCR" num arquivo que já estava na fila do OCR;
- o link "Texto" aparecia no arquivo parcial com o texto ainda incompleto.

A situação nova não pode entrar no grupo "em andamento" (`EM_ANDAMENTO`): o `ingerir_lote` só pega
os arquivos desse grupo, e uma mensagem repetida baixaria do tribunal de novo um arquivo que já
está guardado ([0006](0006-tarefas-longas-e-idempotencia.md), [0016](0016-uma-requisicao-por-vez-ao-tribunal.md)).
Também não pode contar como concluída.

## Decisão
- **Situação `reconhecendo` ("Reconhecendo texto")** no `ArquivoIngerido`. O `extrair_e_salvar` a
  usa quando há páginas para OCR e o OCR está ligado (`INGESTAO_OCR`). A mensagem diz quantas e
  quais páginas estão na fila (ex.: "3 de 40 páginas na fila do OCR (páginas 2–4).").
- **Fora de `EM_ANDAMENTO` e de `CONCLUIDOS`.** Um grupo novo, `PROCESSANDO` (em andamento mais
  reconhecendo), é o que a tela acompanha.
- **Tela do lote** ([0011](0011-acompanhamento-do-lote-na-tela.md)):
  - o resumo continua a ser consultado a cada 3 s enquanto houver arquivo em `PROCESSANDO`;
  - o resumo mostra "N reconhecendo texto" e a lista de lotes mostra a etiqueta "reconhecendo
    texto";
  - a barra conta só os concluídos;
  - o `HX-Refresh` vem quando o OCR do último arquivo termina.
- **Saída garantida:** o `reconhecer_arquivo` recalcula a situação pelas páginas ao terminar e
  também quando o OCR falha (`finally`). As páginas que não foram lidas continuam marcadas para OCR
  e o arquivo volta a "Texto parcial" ou "Sem texto". As já lidas ficam gravadas. Uma mensagem
  repetida de um arquivo sem páginas pendentes só recalcula a situação.
- **Arquivo preso** (mensagem perdida da fila `ocr`): `reextrair --situacao reconhecendo` extrai de
  novo e põe o arquivo outra vez na fila. O `reextrair --todos` sem `--situacao` não mexe nesses
  arquivos, para não duplicar um OCR em curso.

## Alternativas consideradas
- **Manter "Texto parcial"/"Sem texto" e só acrescentar um aviso:** não resolveria a tela que dá o
  lote por concluído, nem a consulta do andamento que para cedo.
- **Pôr `reconhecendo` em `EM_ANDAMENTO`:** a tela funcionaria sem um grupo novo, mas o
  `ingerir_lote` voltaria a pedir ao tribunal o documento já baixado (mensagem repetida ou
  retomada), contra a [0016](0016-uma-requisicao-por-vez-ao-tribunal.md).
- **Situação por lote** (ex.: "lote em OCR"): o lote não tem situação própria, que é derivada da
  contagem dos arquivos. Manter assim evita dois lugares para o mesmo estado.
- **Mostrar o progresso página a página do OCR no resumo:** exigiria contar as páginas a cada 3 s
  em lotes que podem ter milhares delas. A contagem de arquivos já basta para o usuário. O total
  "para OCR" de cada arquivo continua na lista quando a página é recarregada.

## Motivos
O usuário vê, sem recarregar a página, que o lote ainda está trabalhando e em que fase. A
idempotência do download não muda, e nenhum arquivo fica preso em "reconhecendo" por falha do
motor.

## Consequências
- Migração `0005_arquivo_reconhecendo` (só as opções do campo `situacao`).
- O link "Texto" de um arquivo com OCR só aparece depois do OCR (`COM_TEXTO` não muda).
- Com o OCR desligado (testes, execução fora do Docker), nada muda: o arquivo continua como
  "Texto parcial" ou "Sem texto" e não vai para a fila.
- Se o `worker-ocr` ficar parado, os arquivos ficam em "reconhecendo" e a tela continua a
  consultar o andamento até ele voltar.
