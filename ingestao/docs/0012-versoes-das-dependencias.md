# 0012. Versões das dependências novas

- **Status:** Aceita
- **Data:** 2026-11-23
- **Origem:** proposta técnica aceita

## Contexto
O módulo trouxe duas dependências diretas, Celery e pypdf, além das dependências indiretas do
Celery. O projeto roda em Python 3.14 e fixa no `requirements.txt` a versão exata de todos os
pacotes, inclusive os indiretos.

## Decisão
- **Regra:** só adotar versões publicadas há pelo menos duas semanas e testadas no Python 3.14
  num container descartável antes de entrar no projeto.
- **Celery 5.7.0** (publicada em 05/11/2026), com kombu 5.7.0, billiard 4.3.2, amqp 5.4.2 e vine
  5.1.0.
- **pypdf 6.21.4** (28/10/2026), e não a série 7, lançada em 06/11/2026 (7.0.0) e ainda com
  versões novas saindo a cada poucos dias.
- **wcwidth 0.9.4** (24/10/2026), e não a 0.9.5, publicada dois dias antes da decisão.
- As demais dependências indiretas (click e complementos, prompt_toolkit, python-dateutil, six,
  tzlocal) na versão instalada pelo pip, todas com mais de duas semanas.

## Alternativas consideradas
- **Sempre a versão mais recente:** expõe o projeto a versões recém-lançadas, com mais chance de
  regressões ainda não corrigidas.
- **Não fixar as indiretas:** cada build poderia trazer versões diferentes.

## Motivos
Versões com algumas semanas de uso já tiveram a chance de revelar os problemas mais graves, e
fixar todas as versões mantém os builds reproduzíveis.

## Consequências
- Atualizar uma dependência é uma decisão explícita: conferir a data, testar e alterar o
  `requirements.txt`.
- Migrar para o pypdf 7 deve ser reavaliado quando a série estabilizar.
