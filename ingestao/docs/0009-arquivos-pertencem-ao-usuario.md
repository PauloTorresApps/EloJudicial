# 0009. Lotes e arquivos pertencem ao usuário

- **Status:** Aceita
- **Data:** 2026-11-23
- **Origem:** escolha do responsável pelo projeto

## Contexto
Os documentos são baixados com as credenciais de um usuário no tribunal, que decide o que esse
usuário pode ver, inclusive documentos sigilosos. Outros usuários do EloJudicial podem consultar
o mesmo processo.

## Decisão
Cada lote e cada arquivo pertencem só ao usuário que criou o lote, como já acontece com os
avisos pendentes:
- todas as views filtram pelo dono (`usuario=request.user`); para outro usuário, a resposta é
  404, sem revelar que o lote existe;
- os modelos não são registrados no admin, como as credenciais.

## Alternativas consideradas
- **Compartilhado por processo:** um documento já ingerido por alguém seria reaproveitado por
  outros usuários do mesmo processo. Economizaria chamadas ao tribunal, mas misturaria acessos:
  um usuário poderia ver um documento sigiloso que o tribunal só liberou para outro.

## Motivos
Quem decide o acesso a cada documento é o tribunal, para cada credencial. Compartilhar quebraria
essa regra.

## Consequências
- Dois usuários que ingerem o mesmo documento fazem duas chamadas ao tribunal e guardam duas
  cópias.
- O SHA-256 guardado por arquivo (ver [0007](0007-armazenamento-em-volume-em-disco.md)) permite
  evitar reprocessar documentos iguais no futuro, sem compartilhar o acesso.
