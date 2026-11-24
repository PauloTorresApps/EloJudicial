# 0005. Acesso ao tribunal pelo worker sem persistir a senha

- **Status:** Aceita
- **Data:** 2026-11-23
- **Origem:** proposta técnica aceita

## Contexto
Para baixar um documento, o worker precisa das credenciais do usuário no tribunal. O
EloJudicial nunca guarda a senha: guarda só o hash SHA-256, no Redis sem persistência, numa
chave ligada à sessão de login e ao sistema (`judicial/senhas.py`), com prazo
`MNI_SENHA_TTL` (2 h por padrão). O worker não tem a sessão do usuário.

## Decisão
Ao criar ou retomar um lote, o hash da senha da sessão é **copiado para uma chave do lote**
(`mni:ingestao:<id do lote>`) no Redis sem persistência, com o mesmo prazo da senha
(`ingestao/acesso.py`). O worker lê essa chave na hora de executar cada tarefa.

A chave é apagada quando:
- o tribunal recusa a senha (o lote inteiro passa a "Aguardando senha");
- o usuário sai do EloJudicial (sinal `user_logged_out`, para os lotes em andamento);
- o prazo expira.

Sem a chave, o arquivo fica "Aguardando senha", e o usuário retoma o lote depois de informar a
senha de novo (botão "Retomar").

## Alternativas consideradas
- **Mandar o hash na mensagem do Celery:** a fila é persistida em disco (ver
  [0003](0003-redis-separado-para-a-fila.md)). Descartada.
- **Mandar a chave da sessão para o worker montar a chave da senha:** a chave da sessão equivale
  ao cookie de login do usuário; levá-la para a fila persistida daria acesso à conta.
- **Guardar a senha (cifrada) no banco para o worker:** contraria a regra do projeto de nunca
  persistir a senha.

## Motivos
O worker ganha acesso só ao que precisa (o hash, para o tribunal daquele lote), pelo mesmo prazo
que o usuário já tinha, sem que nada sensível vá para disco.

## Consequências
- Lotes longos podem parar no meio, se a senha expirar ou o usuário sair. É o comportamento
  esperado; o usuário retoma.
- O login usado fica registrado no lote (`LoteIngestao.login`), para baixar todos os arquivos
  com a mesma credencial.
- Uma recusa de senha descarta a cópia do lote, mas não a senha da sessão do usuário, que segue
  o fluxo da tela.
