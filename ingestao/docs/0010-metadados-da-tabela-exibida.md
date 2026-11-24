# 0010. Metadados dos documentos vêm da tabela exibida

- **Status:** Aceita
- **Data:** 2026-11-23
- **Origem:** proposta técnica aceita

## Contexto
O formulário de ingestão envia só os ids dos documentos marcados. Para mostrar o lote com nome,
descrição e tipo de cada documento, e para garantir que os ids pertencem ao processo, é preciso
conhecer os documentos do processo.

Consultar o processo inteiro de novo no tribunal seria pesado: processos com milhares de
eventos podem passar do limite de resposta, e aí a consulta é repetida sem a lista de
documentos.

## Decisão
Ao montar a tabela de eventos, a view guarda no cache (Redis sem persistência), com o prazo da
senha, os metadados dos documentos exibidos: `{id: (nome, descrição, tipo local)}`, por usuário,
ambiente e processo (`judicial/selecao_documentos.py`).

Ao criar o lote, a view de ingestão:
- só aceita ids que estão nessa lista, sem repetição e na ordem da tabela;
- preenche nome, descrição e tipo de cada arquivo a partir dela;
- se a lista tiver expirado, não cria o lote e pede para conferir a seleção de novo.

## Alternativas consideradas
- **Reconsultar o processo no tribunal ao criar o lote:** dados sempre atuais, mas uma chamada
  pesada a mais por lote, que pode falhar em processos grandes.
- **Enviar os metadados no formulário:** multiplicaria o tamanho da página (milhares de
  documentos) e os dados viriam do navegador, sem garantia.
- **Aceitar qualquer id enviado:** o tribunal ainda controlaria o acesso, mas o lote poderia
  conter ids que o usuário nunca viu.

## Motivos
Os dados já estão em memória quando a tabela é montada, e usá-los evita carga extra no
tribunal. A validação garante que só entra no lote o que o usuário viu na tela.

## Consequências
- Um item a mais no cache por processo aberto (da ordem de centenas de KB para milhares de
  documentos), que expira com a senha.
- O cache sem persistência passa a guardar também nomes e descrições de documentos, dado do
  processo da mesma natureza do que ele já guardava.
- Se o usuário deixar a página aberta além do prazo da senha, precisa reabrir o processo.
