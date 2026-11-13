# EloJudicial

Sistema Django para integração com sistemas judiciais (MNI), executado com Docker Compose.

## Serviços

| Serviço   | Função                                                                 |
|-----------|------------------------------------------------------------------------|
| `web`     | Aplicação Django (runserver em dev, gunicorn em produção)              |
| `db`      | PostgreSQL 17                                                          |
| `redis`   | Cache em memória (hash das senhas dos sistemas judiciais e WSDLs)      |
| `nginx`   | Único ponto de entrada externo (portas 80 e 443)                       |
| `mailpit` | Captura os e-mails enviados (somente em desenvolvimento)               |

## Primeiros passos (desenvolvimento)

### 1. Pré-requisitos

- Docker e Docker Compose v2

### 2. Variáveis de ambiente (opcional)

Em desenvolvimento, os valores padrão do `compose.yaml` já funcionam (usuário, senha e
banco `elojudicial`). Para customizar:

```bash
cp .env.example .env
```

> **Atenção:** o Postgres só lê `POSTGRES_USER`, `POSTGRES_PASSWORD` e `POSTGRES_DB` na
> **primeira** inicialização do volume. Se você alterar esses valores depois, o container
> `web` falhará com `password authentication failed`. Nesse caso, recrie o volume
> (apaga os dados) ou ajuste o usuário/senha diretamente no Postgres.

### 3. Subir os containers

```bash
docker compose up -d --build
```

O `compose.override.yaml` é aplicado automaticamente e ativa o modo de desenvolvimento
(`DEBUG=True`, `runserver` com recarga automática, código montado em `/app` e Mailpit).

As migrações são executadas automaticamente na inicialização do `web`
(desative com `DJANGO_MIGRATE=0`).

Verifique se tudo subiu:

```bash
docker compose ps
docker compose logs -f web
```

### 4. Criar o usuário administrador

```bash
docker compose exec -e DJANGO_SUPERUSER_PASSWORD=admin web \
  python manage.py createsuperuser --noinput --username admin --email admin@localhost
```

Credenciais padrão de desenvolvimento:

- **Usuário:** `admin`
- **Senha:** `admin`

Para trocar a senha:

```bash
docker compose exec web python manage.py changepassword admin
```

> Nunca use essas credenciais em produção. Lá, rode `createsuperuser` de forma interativa.

### 5. Acessar

| Endereço                      | O quê                                         |
|-------------------------------|-----------------------------------------------|
| https://localhost             | Sistema                                       |
| https://localhost/admin/      | Admin do Django                               |
| http://localhost:8025         | Mailpit (e-mails enviados)                    |

O HTTP (porta 80) redireciona para HTTPS. Sem certificados em `docker/nginx/certs`, o nginx
gera um certificado autoassinado, então o navegador exibirá um aviso de segurança.

## Comandos úteis

```bash
docker compose exec web python manage.py migrate        # aplicar migrações
docker compose exec web python manage.py makemigrations # gerar migrações
docker compose exec web python manage.py shell          # shell do Django
docker compose exec web python manage.py test           # rodar os testes
docker compose down                                     # parar os containers
docker compose down -v                                  # parar e APAGAR os volumes (banco)
```

## Produção

Ignore o override de desenvolvimento:

```bash
cp .env.example .env   # preencha com valores reais
docker compose -f compose.yaml up -d --build
```

Coloque `fullchain.pem` e `privkey.pem` na pasta indicada por `NGINX_CERTS_DIR`.
