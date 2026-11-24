# 0011. Acompanhamento do lote na tela

- **Status:** Aceita
- **Data:** 2026-11-23
- **Origem:** proposta técnica aceita

## Contexto
Um lote pode ter milhares de arquivos e levar muito tempo. O usuário precisa acompanhar o
andamento, mas redesenhar a lista inteira a cada poucos segundos pesaria no servidor e no
navegador. Na tabela de eventos, que já tem busca e paginação, o formulário de ingestão não pode
atrapalhar a busca.

## Decisão
- **Na tabela de eventos:** um formulário envolve só a tabela, e o botão "Ingerir selecionados"
  fica no cabeçalho, ligado a ele pelo atributo `form`. O botão fica desabilitado sem arquivos
  marcados.
- **Na página do lote:** só o resumo (contagens e barra de progresso) é consultado a cada 3
  segundos (htmx). A lista de arquivos é desenhada uma vez. Quando o lote termina, a resposta do
  resumo pede ao navegador que recarregue a página (`HX-Refresh`), que então mostra a situação
  final de cada arquivo e para de consultar.
- **Retomar:** reenfileira os arquivos que aguardam senha ou falharam. Sem senha na sessão, o
  botão abre o modal de senha.

## Alternativas consideradas
- **Atualizar a lista inteira a cada 3 s:** mostra o andamento arquivo a arquivo, mas redesenha
  milhares de linhas repetidamente.
- **Formulário envolvendo toda a seção de eventos:** um Enter no campo de busca enviaria a
  ingestão.

## Motivos
Dá ao usuário o andamento que importa (quanto falta, quantos falharam) com custo fixo por
consulta, independente do tamanho do lote.

## Consequências
- Durante o processamento, a situação individual de cada arquivo só aparece depois que o lote
  termina, ou ao recarregar a página.
- O corpo da tabela usa `{% spaceless %}`, como a tabela de eventos, porque um lote pode ter
  milhares de linhas.
