# Fluxo da ingestão de documentos

Como um documento sai do tribunal e vira texto guardado no EloJudicial: do clique em "Ingerir"
na tabela de eventos até as páginas extraídas no PostgreSQL. As decisões por trás de cada etapa
estão nos ADRs deste diretório (ver o [índice](README.md)).

Situação em 2026-11-25. Os diagramas usam [Mermaid](https://mermaid.js.org/), que o GitHub e o
VS Code exibem direto no Markdown.

## 1. Visão geral

```mermaid
flowchart LR
    U(["Usuário na tabela de eventos"]) -->|"marca os documentos e clica em Ingerir"| V["views.ingerir"]

    subgraph WEB["Servidor web (gunicorn)"]
        V
        TL["Tela do lote<br/>andamento a cada 3 s (htmx)"]
    end

    V -->|"lote + 1 arquivo por documento"| PG[("PostgreSQL")]
    V -->|"hash da senha<br/>mni:ingestao:&lt;lote&gt;"| RC[("Redis de cache<br/>sem persistência")]
    V -->|"1 mensagem por lote<br/>só ids"| RF[("Redis da fila<br/>com persistência")]

    subgraph WK["Worker Celery (fila ingestao)"]
        T["ingerir_lote"]
        EX["Extração de texto<br/>por página"]
    end

    RF --> T
    T -->|"lê o hash"| RC
    T -->|"vez do tribunal"| LIM{{"Limitador<br/>1 por vez · 2 s"}}
    LIM <-->|"trava mni:trava:&lt;tribunal&gt;"| RC
    LIM -->|"1 envelope com todos os ids"| TRIB[["Tribunal (MNI)"]]
    TRIB -->|"conteúdo base64 ou MTOM"| T
    T -->|"original"| VOL[("Volume ingestao<br/>&lt;usuário&gt;/&lt;lote&gt;/&lt;arquivo&gt;.ext")]
    T --> EX
    EX -->|"páginas e situação"| PG

    PG --> TL
    TL -->|"original e texto"| U
```

## 2. Download: da criação do lote à resposta do tribunal

Um lote comum gera **uma única requisição** ao tribunal
([0017](0017-download-do-lote-numa-unica-requisicao.md)). Toda requisição passa pelo limitador
([0016](0016-uma-requisicao-por-vez-ao-tribunal.md)).

```mermaid
sequenceDiagram
    autonumber
    actor U as Usuário
    participant W as views.ingerir
    participant PG as PostgreSQL
    participant RC as Redis de cache
    participant F as Fila (Redis)
    participant T as ingerir_lote (worker)
    participant L as Limitador
    participant M as Tribunal (MNI)

    U->>W: POST com os documentos marcados
    W->>RC: documentos_lembrados (tabela exibida)
    Note over W: só ids que estavam na tabela,<br/>sem repetição, na ordem da tabela
    W->>PG: cria LoteIngestao + ArquivoIngerido (na fila)
    W->>RC: guarda o hash da senha na chave do lote
    W-->>F: após o commit: ingerir_lote(lote_id, ids)
    W->>U: redireciona para a tela do lote

    F->>T: entrega a mensagem
    T->>PG: SELECT FOR UPDATE dos arquivos em andamento → "baixando"
    T->>RC: lê o hash da senha do lote
    alt senha expirada ou ausente
        T->>PG: arquivos → "aguardando senha"
    else senha disponível
        T->>L: vez_do_tribunal (espera até 3 min)
        L->>RC: trava do tribunal + 2 s desde a última requisição
        T->>M: consultarDocumentosProcesso (3.0) ou consultarProcesso (2.2)<br/>com um idDocumento/documento por arquivo
        alt resposta acima de 100 MB
            M-->>T: RespostaGrandeDemais
            Note over T: divide o grupo ao meio e pede cada metade<br/>(um documento sozinho grande demais → erro)
        else senha recusada
            M-->>T: falha de autenticação
            T->>RC: apaga a chave do lote
            T->>PG: lote inteiro → "aguardando senha" (sem novos pedidos)
        else falha de comunicação
            M-->>T: ErroMNI
            T->>PG: o que faltou → "na fila"
            T-->>F: nova tentativa só com o que faltou (30 s, 60 s, 120 s)
            Note over T: depois da 3ª nova tentativa → "erro"
        else sucesso
            M-->>T: documentos (base64 no XML ou anexos MTOM)
            Note over T: associa cada conteúdo ao arquivo pelo id,<br/>o que não veio vai para "erro"
            T->>T: guarda o original e extrai o texto (seção 3)
        end
    end

    loop a cada 3 s, enquanto houver arquivos em andamento
        U->>W: tela do lote / andamento
        W->>PG: contagem por situação
    end
```

## 3. Extração: do original às páginas classificadas

Feita no worker logo depois do download, arquivo por arquivo
([0008](0008-extracao-de-texto-sem-ocr.md), [0013](0013-deteccao-de-texto-por-pagina.md)). Não
há OCR: as páginas sem texto aproveitável ficam marcadas para o OCR futuro.

```mermaid
flowchart TD
    D["Conteúdo recebido do tribunal"] --> G["Guarda o original no volume<br/>+ tipo, codificação, tamanho e SHA-256 no arquivo"]
    G --> TIPO{"Tipo do arquivo<br/>(mimetype)"}

    TIPO -->|"application/pdf"| PDF["pypdf: texto nativo de cada página<br/>+ tamanho da maior imagem"]
    TIPO -->|"text/html"| HTML["Texto sem script, estilo e título;<br/>parágrafos separados"]
    TIPO -->|"text/*"| TXT["Texto decodificado e normalizado"]
    TIPO -->|"image/*"| IMG["1 página sem texto<br/>(vai para OCR)"]
    TIPO -->|"outro tipo ou PDF ilegível"| NADA["Sem páginas<br/>+ aviso ao usuário"]

    PDF --> C
    HTML --> C
    TXT --> C
    C["Classifica cada página<br/>caracteres · qualidade · maior imagem"]

    C --> R1{"menos de 20 caracteres?"}
    R1 -->|"sim, com imagem grande"| SEM["sem_texto<br/>(digitalizada)"]
    R1 -->|"sim, sem imagem grande"| BR["em_branco"]
    R1 -->|"não"| R2{"imagem grande e<br/>menos de 300 caracteres?"}
    R2 -->|"sim (scan com carimbo)"| SEM
    R2 -->|"não"| R3{"qualidade &lt; 0,6?"}
    R3 -->|"sim"| RUIM["texto_ruim"]
    R3 -->|"não"| COM["com_texto"]

    IMG --> P
    SEM --> P
    BR --> P
    RUIM --> P
    COM --> P
    P[("PaginaExtraida<br/>1 linha por página")] --> S
    NADA --> S

    S{"Situação do arquivo"}
    S -->|"sem páginas"| AS["Sem texto<br/>(com o aviso)"]
    S -->|"nenhuma página para OCR<br/>e alguma com texto"| AP["Pronto"]
    S -->|"nenhuma para OCR e nenhuma com texto"| AB["Sem texto<br/>(em branco)"]
    S -->|"há páginas para OCR<br/>e nenhuma com texto"| AO["Sem texto<br/>(precisa de OCR)"]
    S -->|"há páginas para OCR<br/>e alguma com texto"| APC["Texto parcial"]
```

Os limiares (20 e 300 caracteres, imagem grande a partir de 500 mil pixels, qualidade 0,6) são
provisórios: ficam em `Limiares` (`ingestao/extracao.py`) e são calibrados com
`estatisticas_extracao` e `reextrair` ([0014](0014-medicao-e-calibracao-dos-limiares.md)).
`reextrair` reprocessa os originais já guardados, sem consultar o tribunal.

## 4. Situações de um arquivo

```mermaid
stateDiagram-v2
    [*] --> pendente: lote criado ("Na fila")
    pendente --> baixando: worker pega o arquivo
    baixando --> pronto: texto em todas as páginas úteis
    baixando --> parcial: parte das páginas precisa de OCR
    baixando --> sem_texto: nenhuma página com texto aproveitável
    baixando --> erro: não veio na resposta, grande demais<br/>ou falhas esgotadas
    baixando --> pendente: falha de comunicação (nova tentativa)
    baixando --> aguardando_senha: senha expirada ou recusada
    pendente --> aguardando_senha: tribunal recusou a senha do lote
    aguardando_senha --> pendente: usuário informa a senha e retoma
    erro --> pendente: usuário retoma o lote
    pronto --> [*]
    parcial --> [*]
    sem_texto --> [*]
```

"Na fila" e "baixando" contam como **em andamento**. A tarefa só pega arquivos nessas
situações, o que a torna idempotente: uma mensagem repetida não baixa de novo o que já terminou
([0006](0006-tarefas-longas-e-idempotencia.md)).

## 5. Onde cada dado fica guardado

```mermaid
erDiagram
    USUARIO ||--o{ LOTE_INGESTAO : "cria (só ele vê)"
    AMBIENTE ||--o{ LOTE_INGESTAO : "de onde vem"
    LOTE_INGESTAO ||--|{ ARQUIVO_INGERIDO : "contém"
    ARQUIVO_INGERIDO ||--o{ PAGINA_EXTRAIDA : "tem"
    ARQUIVO_INGERIDO ||--o| ORIGINAL_NO_VOLUME : "aponta para"

    LOTE_INGESTAO {
        int id PK
        int usuario_id FK
        int ambiente_id FK
        string numero_processo "20 dígitos"
        string login "usuário no tribunal"
        datetime criado_em
    }
    ARQUIVO_INGERIDO {
        int id PK
        int lote_id FK
        string id_documento "único por lote"
        string nome "da tabela exibida"
        string descricao
        string tipo_local "código do tipo no tribunal"
        string situacao "pendente, baixando, pronto..."
        text mensagem "motivo do erro ou do OCR"
        file arquivo "caminho no volume"
        string mimetype
        string codificacao
        bigint tamanho "bytes"
        string sha256
    }
    PAGINA_EXTRAIDA {
        int id PK
        int arquivo_id FK
        int numero "único por arquivo"
        string situacao "com_texto, em_branco, sem_texto, texto_ruim"
        string metodo "nativo (OCR no futuro)"
        text texto
        int caracteres "sem espaços"
        float qualidade "0 a 1"
        bigint maior_imagem "pixels"
    }
    ORIGINAL_NO_VOLUME {
        path caminho "usuario/lote/arquivo.ext"
        bytes conteudo "exatamente como veio do tribunal"
    }
```

| Dado | Onde | Por quanto tempo |
|------|------|------------------|
| Lote, arquivos (metadados, situação, SHA-256) e páginas (texto e métricas) | PostgreSQL | Até o lote ser apagado |
| Original de cada arquivo | Volume `ingestao` (`INGESTAO_DIR`), em `<usuário>/<lote>/<arquivo>.<ext>` | Até o arquivo ser apagado: apagar o registro apaga o arquivo do volume |
| Hash da senha do tribunal para o lote (`mni:ingestao:<lote>`) | Redis de cache, sem persistência | `MNI_SENHA_TTL` (2 h); apagado se o tribunal recusar a senha ou o usuário sair |
| Trava e hora da última requisição ao tribunal (`mni:trava:<tribunal>`, `mni:ultima:<tribunal>`) | Redis de cache | Trava: até a requisição terminar (no máximo 5 min) |
| Mensagem da tarefa (`ingerir_lote(lote_id, ids)`) | Redis da fila, com persistência | Até a tarefa terminar; nunca leva senha nem conteúdo |

O **texto do arquivo inteiro** não é guardado à parte: é montado na hora, juntando o texto das
páginas em ordem, separadas por uma linha em branco (`ArquivoIngerido.texto`). A tela do lote
entrega o original (`/ingestao/arquivos/<id>/`) e o texto (`/ingestao/arquivos/<id>/texto/`),
sempre só para o dono do lote ([0009](0009-arquivos-pertencem-ao-usuario.md)).
