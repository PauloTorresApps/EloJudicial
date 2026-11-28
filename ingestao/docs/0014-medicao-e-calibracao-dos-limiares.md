# 0014. Medição e calibração dos limiares

- **Status:** Aceita (complementada por [0022](0022-metricas-das-leituras-do-ocr.md): métricas das leituras do OCR)
- **Data:** 2026-11-24
- **Origem:** proposta técnica aceita (passo 2 da cascata de extração)

## Contexto
Os limiares da detecção por página (decisão [0013](0013-deteccao-de-texto-por-pagina.md)) foram
escolhidos sem dados reais. Antes de escolher um motor de OCR e decidir sobre o modelo multimodal
(passo 3), é preciso saber, com documentos reais do MPTO:
- que proporção das páginas precisa de OCR (o quanto o OCR vale a pena);
- se os limiares classificam bem (falsos "com texto" perdem conteúdo; falsos "precisa de OCR"
  só custam processamento).

Os documentos só podem ser baixados com credenciais reais nos tribunais, então a medição
acontece no ambiente do projeto, a partir das ingestões que os usuários fizerem.

## Decisão
1. **Guardar as métricas de cada página** (caracteres, qualidade do texto e maior imagem), e não
   só a classificação.
2. **Comando `estatisticas_extracao`**: relatório de contagens e distribuições.
   - Mostra arquivos por situação e por tipo, páginas por situação e por sistema, e
     distribuições de caracteres (separando páginas com e sem imagem grande), de qualidade do
     texto e de tamanho da maior imagem.
   - Filtros: `--sistema` e `--desde`. Saída em texto ou `--json`.
   - **Sem conteúdo**: nenhum texto, nome de documento, processo ou usuário sai no relatório.
3. **Simulação de limiares** (`--simular min_caracteres_com_imagem=500 …`): reclassifica as
   métricas guardadas com outros valores e mostra quantas páginas mudariam de situação, sem
   reprocessar nada. Imagens avulsas ficam fora da simulação, porque sempre vão para OCR.
4. **Comando `reextrair`**: depois de mudar os limiares (ou a extração) no código, reaplica a
   extração aos originais já guardados, sem consultar o tribunal, e informa quantos arquivos
   mudaram de situação.

**Ciclo de calibração:** ingerir processos reais de diferentes sistemas → `estatisticas_extracao`
→ simular valores → conferir por amostragem algumas páginas classificadas → mudar os padrões de
`Limiares` (registrando a mudança como nova decisão) → `reextrair --todos`.

## Alternativas consideradas
- **Tela de estatísticas no sistema:** mais acessível, mas os lotes pertencem a cada usuário
  (decisão [0009](0009-arquivos-pertencem-ao-usuario.md)). Um relatório agregado na linha de
  comando, para quem opera o servidor, evita expor dados de uns usuários a outros.
- **Guardar só a classificação:** para testar outro limiar, seria preciso reprocessar todos os
  arquivos.
- **Calibrar a partir de um conjunto de teste montado à mão:** útil depois, mas não diz qual é a
  proporção real de páginas escaneadas no acervo dos usuários.

## Motivos
A decisão sobre OCR e sobre o modelo multimodal envolve custo e sigilo. Ela deve partir de
números do acervo real, e não de suposições. Simular sobre métricas guardadas torna a
calibração barata e repetível.

## Consequências
- **O passo 3 depende de ingestões reais.** Os comandos estão prontos, mas os números só aparecem
  depois que usuários ingerirem processos de verdade, idealmente de mais de um sistema (eproc,
  PJe, e-SAJ), porque os geradores de PDF variam entre tribunais.
- A conferência por amostragem das páginas classificadas é manual e respeita a regra de acesso:
  quem confere só abre os lotes a que tem direito.
- Cada mudança de limiar após a calibração é registrada como nova decisão, com os números que a
  justificaram.
