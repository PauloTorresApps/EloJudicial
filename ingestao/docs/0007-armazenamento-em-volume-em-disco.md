# 0007. Originais num volume em disco

- **Status:** Aceita
- **Data:** 2026-11-23
- **Origem:** escolha do responsável pelo projeto

## Contexto
Os documentos ingeridos são documentos judiciais, alguns sigilosos, e podem ter até 100 MB
(limite já usado na consulta de documentos). Precisam ser guardados para a análise posterior e
acessados pelo `web` (para o usuário abrir) e pelo `worker` (que grava).

## Decisão
Os originais ficam num **volume Docker dedicado** (`ingestao`), montado em
`/var/lib/elojudicial/ingestao` no `web` e no `worker`, por meio do armazenamento do Django de
nome `ingestao` (`STORAGES['ingestao']`, `FileSystemStorage`, pasta em `INGESTAO_DIR`).

- O caminho de cada arquivo é `<id do usuário>/<id do lote>/<id do arquivo><extensão>`.
- A pasta não é servida pelo nginx nem pelo whitenoise: os arquivos só saem pelas views, para o
  dono (ver [0009](0009-arquivos-pertencem-ao-usuario.md)).
- Cada arquivo guarda o SHA-256 do conteúdo, para conferir integridade e, no futuro, evitar
  reprocessar documentos iguais.
- Apagar um registro (ou o lote, ou o usuário) apaga o arquivo do volume.

## Alternativas consideradas
- **No PostgreSQL (`BinaryField`):** backup único junto com os dados, mas o banco cresceria
  muito e mais devagar a cada lote, e cada leitura traria o conteúdo inteiro para a memória.
- **Object storage (S3/MinIO) desde já:** mais preparado para escala, mas exige subir um MinIO
  no compose ou ter um bucket disponível.

## Motivos
É a opção mais simples hoje que não fecha a porta: como o acesso é pelo armazenamento do
Django, trocar para S3/MinIO depois é mudar a configuração de `STORAGES['ingestao']`, sem mexer
no código do módulo.

## Consequências
- O volume `ingestao` precisa entrar na rotina de backup, separado do backup do banco.
- O `Dockerfile` cria a pasta com o dono `app`, para que o volume nasça com a permissão certa.
- **Testes:** o `FileField` resolve o armazenamento quando o modelo é carregado, então
  `override_settings(STORAGES=...)` não tem efeito nele. Os testes trocam o armazenamento do
  próprio campo por uma pasta temporária (`ingestao/tests/test_ingestao.py`). Na primeira
  versão isso não era feito, e os testes chegaram a gravar arquivos de exemplo no volume de
  desenvolvimento, que foram apagados.
- A retenção (por quanto tempo guardar) ainda está em aberto (ver o [índice](README.md)).
