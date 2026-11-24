# 0015. Revisão de simplificação do módulo (fim dos passos 1 e 2 da cascata)

- **Status:** Aceita
- **Data:** 2026-11-24
- **Origem:** proposta técnica aceita (revisão `/simplify` pedida pelo responsável pelo projeto)

## Contexto
Com os passos 1 (detecção por página, [0013](0013-deteccao-de-texto-por-pagina.md)) e 2
(medição, [0014](0014-medicao-e-calibracao-dos-limiares.md)) prontos, o módulo foi revisado
antes do primeiro commit, sob quatro ângulos: reuso do que o projeto já tem, simplificação,
eficiência e "altitude" (se cada coisa está no nível certo). O objetivo era só qualidade, sem
mudar o comportamento. Este registro guarda o que mudou, o que ficou de fora e por quê, para a
análise posterior.

## Decisão
Aplicado:
- **As situações de página têm uma única definição**, em `extracao.py`, onde fica a regra de
  classificação. `PaginaExtraida.Situacao` e `PaginaExtraida.PRECISAM_OCR` usam esses valores.
- **O grupo "tem texto" fica no modelo:** `ArquivoIngerido.COM_TEXTO` (Pronto, Texto parcial) e
  a propriedade `tem_texto`. O template não compara mais situações escritas à mão.
- **Funções do `judicial` usadas pela ingestão passaram a ser públicas:** `numero_valido`,
  `ambiente_ativo` e `credencial_do_usuario` (antes `_numero_valido`, `_ambiente_ativo`,
  `_credencial`). A ingestão não depende mais de funções internas de outro app.
- **O texto extraído é entregue por `resposta_documento`**, a mesma função do original e dos
  documentos do tribunal, com os mesmos cabeçalhos de segurança.
- **Views:** a consulta de lotes com as contagens fica num helper só (lista e detalhe); plural
  com o `pluralize` do Django; os metadados lembrados são lidos por nome, e não por índice.
- **Extração:** a busca da maior imagem virou uma única função recursiva; `classificar` perdeu
  o parâmetro `limiares`, que nunca era usado (a simulação usa `situacao_da_pagina`); os
  caracteres sem espaço são contados uma vez por página.
- **Gravação das páginas em blocos de 500** (`bulk_create(batch_size=500)`).
- **`situacao_do_arquivo` e `_paginas`** ficaram com retornos diretos, sem ramo morto.
- **`estatisticas_extracao`:** contador próprio para imagens avulsas, total por sistema montado
  já no formato final, rótulos calculados uma vez.
- **`compose.override.yaml`:** removidas as repetições do `compose.yaml` (volume `ingestao` e
  `DJANGO_MIGRATE` do worker). A configuração final, conferida com `docker compose config`, é a
  mesma.

## Alternativas consideradas
Pontos levantados na revisão e **não** aplicados agora:
- **Guardar no arquivo o total de páginas e quantas precisam de OCR**, para a página do lote não
  recalcular a contagem a cada abertura: é um dado derivado a mais. Reavaliar se lotes grandes
  ficarem lentos.
- **Paginar as listas de lotes e de arquivos:** muda o que a tela mostra; fica para quando os
  lotes crescerem.
- **Entregar o original e o texto em streaming:** já estava em aberto (ver README).
- **Enfileirar o lote numa só publicação (`group`)** em vez de uma chamada por arquivo: ganho
  pequeno com a conexão já reaproveitada pelo Celery.
- **Armazenamento resolvido de forma preguiçosa** para os testes usarem `override_settings` em
  vez de trocar o armazenamento do campo: exigiria uma classe própria para um problema já
  resolvido nos testes.
- **Tirar do `judicial` a memória dos documentos exibidos** (`lembrar_documentos`), gravada a
  cada exibição de processo só para a ingestão: mudança de arquitetura, a decidir junto com os
  agentes de IA.
- **Modo síncrono do Celery por variável explícita** (`CELERY_EAGER`) em vez da ausência de
  `REDIS_FILA_URL`: muda o comportamento fora do Docker.
- **Remover os campos `metodo` e `sha256`:** mantidos de propósito. `metodo` vai distinguir texto
  nativo de OCR no passo 3; `sha256` serve para conferir a integridade do original e,
  futuramente, evitar análises repetidas do mesmo documento.
- **Retoques de template** (componente de progresso e botão do Unfold, classes repetidas das
  tabelas): mantido o padrão das telas já existentes.

## Motivos
A revisão foi feita antes do primeiro commit do módulo, para que ele entre no repositório sem
duplicações e com cada regra num lugar só, o que importa para o passo 3, quando surgirão novas
situações (OCR) e novas camadas. Ficaram de fora as mudanças que alterariam o comportamento ou a
arquitetura, que pedem decisão própria.

## Consequências
- Os 277 testes passam; nenhuma migração nova (os valores das situações não mudaram).
- Nova situação de página (por exemplo, a de OCR) é acrescentada só em `extracao.py` e no
  `TextChoices` do modelo.
- **Ponto encontrado e ainda não tratado:** ao sair do sistema, a chave de acesso ao tribunal do
  lote (`ingestao/acesso.py`) só é apagada para lotes com arquivos em andamento. As chaves de
  lotes já concluídos ficam no cache até expirar (`MNI_SENHA_TTL`), o que contraria a promessa
  da documentação do módulo. Sugestão: apagar a chave quando o lote termina. Registrado em
  "Decisões em aberto".
