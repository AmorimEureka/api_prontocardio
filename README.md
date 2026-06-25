# API Prontocardio

![FastAPI](https://img.shields.io/badge/FastAPI-0.136.1-009688?logo=fastapi&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-1F425F?logo=sqlalchemy&logoColor=white)
![Oracle Thick Mode](https://img.shields.io/badge/Oracle-Driver%20Thick%20Mode-F80000?logo=oracle&logoColor=white)
![Sistema MV](https://img.shields.io/badge/ERP-MV%20Sistemas-0052CC)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Ubuntu](https://img.shields.io/badge/Ubuntu-Linux-E95420?logo=ubuntu&logoColor=white)
![SQL](https://img.shields.io/badge/SQL-Oracle%20Database-336791)

## Objetivo

Esta API tem como objetivo principal servir de interface entre dados
armazenados no ERP MV Sistemas e aplicacoes terceiras desenvolvidas internamente.

## Estrutura do projeto

```text
.
├── app_prontocardio/
│   ├── __init__.py
│   ├── app.py
│   ├── database.py
│   ├── models.py
│   ├── schema.py
│   └── settings.py
├── tests/
│   ├── conftest.py
│   └── test_app.py
├── pyproject.toml
├── README.md
└── settings.json
```

## Tecnologias principais

- FastAPI para exposicao dos endpoints HTTP.
- SQLAlchemy 2.x para acesso ao banco Oracle.
- python-oracledb em Thick Mode para compatibilidade com autenticacao Oracle.
- Pydantic para contratos e validacao de entrada/saida.
- Pytest para testes automatizados.
- Ruff para lint e formatacao.

## Endpoints da aplicacao

### GET /

- Objetivo: health-check simples da API.
- Resposta: mensagem institucional.

Exemplo de resposta:

```json
{
	"message": "Ola Mundo! API Hospital Prontocardio"
}
```

### GET /versao_oracle/

- Objetivo: consultar versao do Oracle.
- Fonte: `SELECT * FROM v$version`.
- Contrato de resposta: `list[VersaoOracle]`.

Exemplo de resposta:

```json
[
	{
		"banner": "Oracle Database 19c Enterprise Edition Release ..."
	}
]
```

### GET /conta_atendimento/{cd_atendimento}

- Objetivo: consultar itens de conta por atendimento na view
	`DBAMV.HPC_V_CONTA_ATENDIMENTO`.
- Parametro de rota:
	`cd_atendimento` (inteiro obrigatorio, `gt=0`).
- Contrato de resposta: `Atendimentos` (wrapper com lista de `Atendimento`).

Possiveis respostas:

- `200 OK`: retorna os dados do atendimento encontrado.
- `404 Not Found`: atendimento nao localizado.
- `422 Unprocessable Entity`: parametro de rota invalido.
- `500 Internal Server Error`: erro na consulta ao banco.

Exemplo de resposta:

```json
{
	"atendimentos": [
		{
			"cd_reg": 374713,
			"cd_lancamento": 1,
			"cd_atendimento": 305226,
			"nm_paciente": "PACIENTE EXEMPLO",
			"descricao": "CONSULTA EM PRONTO SOCORRO"
		}
	]
}
```

## Contratos em schema.py e importancia da validacao

O arquivo `app_prontocardio/schema.py` define os contratos de entrada/saida
da API com Pydantic:

- `VersaoOracle`: representa cada linha retornada por `v$version`.
- `Atendimento`: representa um item da view
	`HPC_V_CONTA_ATENDIMENTO`.
- `Atendimentos`: wrapper da resposta do endpoint de conta.

Por que isso e importante:

- Garante formato consistente para consumidores internos.
- Evita retorno de estruturas inesperadas em runtime.
- Documenta automaticamente os contratos no Swagger/OpenAPI.
- Valida e normaliza tipos retornados pela API.

## Models e interacao com banco

O arquivo `app_prontocardio/models.py` contem o mapeamento ORM da view
`DBAMV.HPC_V_CONTA_ATENDIMENTO`:

- Classe `ModelContaAtendimento` com `mapped_as_dataclass`.
- Campos mapeados para tipos Python/SQLAlchemy.
- Base para consultas com `select(ModelContaAtendimento)`.

Esse modelo desacopla SQL literal da logica de negocio e facilita manutencao,
tipagem e evolucao do endpoint.

## Configuracao em settings

O arquivo `app_prontocardio/settings.py` centraliza variaveis de ambiente com
`pydantic-settings`:

- `ORACLE_DATABASE_URL`: conexao com o Oracle/MV via `oracle+oracledb`.
- `DATABASE_URL`: conexao com o PostgreSQL usado pela API.
- `POSTGRES_SCHEMA`: schema PostgreSQL usado pelos models da aplicacao.
- `RUN_MIGRATIONS_ON_STARTUP`: controla se migrations rodam ao iniciar a API.
- `SECRET_KEY` e `ALGORITHM`: assinatura dos tokens JWT.
- `FRONTEND_BASE_URL`: base do frontend usada como fallback de origem.
- `FRONTEND_PASSWORD_RESET_URL`: URL da tela do frontend que recebe o token
	de recuperacao de senha. Se a URL contiver `{token}`, a API substitui
	esse marcador pelo token gerado; prefira `#token={token}` para evitar
	que o token seja enviado ao servidor em logs de acesso.
- `CORS_ALLOWED_ORIGINS`: origens permitidas para chamadas do frontend,
	separadas por virgula. Quando vazia, usa `FRONTEND_BASE_URL`.

Exemplo de `.env`:

```env
ORACLE_DATABASE_URL=oracle+oracledb://usuario:senha@host:1521/?service_name=nome_servico
DATABASE_URL=postgresql+psycopg://usuario:senha@host:5432/banco
POSTGRES_SCHEMA=api_prontocardio
SECRET_KEY=gere_uma_chave_forte
ALGORITHM=HS256
FRONTEND_BASE_URL=http://localhost:8080
FRONTEND_PASSWORD_RESET_URL=http://localhost:8080/autenticacao/redefinir-senha#token={token}
CORS_ALLOWED_ORIGINS=http://localhost:8080
```

## Testes e objetivos

Os testes ficam em `tests/test_app.py` e os fixtures em `tests/conftest.py`.

Testes atuais:

- `test_root`: valida endpoint raiz e payload esperado.
- `test_oracle_conn`: valida conectividade Oracle com `SELECT * FROM v$version`.
- `test_conta_atendimento_found`: garante retorno com sucesso para
	atendimento existente.
- `test_conta_atendimento_not_found`: garante retorno `404` para codigo
	inexistente.
- `test_conta_atendimento_invalid_path`: garante validacao `422` quando
	`cd_atendimento` e invalido.

Fixtures principais:

- `cliente`: instancia `TestClient` da FastAPI.
- `oracle_engine` (scope session): cria engine Oracle em Thick Mode e valida
	conexao inicial.
- `session` (scope session): entrega sessao SQLAlchemy reaproveitada.

## Setup do ambiente de desenvolvimento

### 1) Instalar/atualizar pyenv (Ubuntu)

```bash
curl https://pyenv.run | bash
```

Adicione no shell (`~/.zshrc` ou `~/.bashrc`):

```bash
export PYENV_ROOT="$HOME/.pyenv"
[[ -d $PYENV_ROOT/bin ]] && export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
```

Reabra o terminal e instale Python 3.12:

```bash
pyenv install 3.12.1
pyenv local 3.12.1
python --version
```

### 2) Instalar Poetry

```bash
curl -sSL https://install.python-poetry.org | python3 -
```

Opcional (recomendado): criar venv no proprio projeto:

```bash
poetry config virtualenvs.in-project true
```

### 3) Instalar dependencias e criar ambiente virtual

Na raiz do projeto:

```bash
poetry install
```

Ativar shell do projeto:

```bash
poetry shell
```

### 4) Gerenciamento de dependencias

Adicionar dependencia de runtime:

```bash
poetry add nome_pacote
```

Adicionar dependencia de desenvolvimento:

```bash
poetry add --group dev nome_pacote
```

Atualizar lock/dependencias:

```bash
poetry update
```

### 5) Configurar variaveis de ambiente

Crie o arquivo `.env` na raiz com as variaveis descritas na secao de
configuracao. Para desenvolvimento local, ajuste `ORACLE_DATABASE_URL`,
`DATABASE_URL`, `POSTGRES_SCHEMA`, `SECRET_KEY`, `ALGORITHM`,
`FRONTEND_BASE_URL` e `FRONTEND_PASSWORD_RESET_URL`.

## Execucao da aplicacao

Via Taskipy:

```bash
poetry run task run
```

Documentacao interativa:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

## Ferramentas de qualidade (lint, format, testes)

Comandos definidos em `pyproject.toml`:

- Lint:

```bash
poetry run task lint
```

- Formatacao (com pre-check):

```bash
poetry run task format
```

- Testes com cobertura:

```bash
poetry run task tests
```

- Gerar relatorio HTML de cobertura:

```bash
poetry run task pos_tests
```

## Producao com Docker

O compose de producao sobe a API na porta `8000` por padrao, configuravel por
`API_PORT`. O Nginx/proxy reverso do servidor deve ser configurado fora deste
repositorio e encaminhar trafego para a API na porta publicada ou para
`api_prontocardio:8000` na rede Docker configurada por `API_NETWORK_NAME`.

Configure no `.env` os valores do ambiente:

```env
SERVER_NAME=api.exemplo.local
API_PORT=8000
API_NETWORK_NAME=api_prontocardio
FRONTEND_BASE_URL=https://app.exemplo.local
FRONTEND_PASSWORD_RESET_URL=https://app.exemplo.local/autenticacao/redefinir-senha#token={token}
CORS_ALLOWED_ORIGINS=https://app.exemplo.local
```

Build e execucao padrao:

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Configuracoes locais de Nginx, certificados e ACME nao devem ser versionadas
neste repositorio.

## Observacoes operacionais

- O projeto usa Oracle driver em `thick_mode=True` para compatibilidade com
	cenarios de autenticacao do ambiente hospitalar.
- Antes de subir em producao, valide conectividade com Oracle, PostgreSQL e
	variaveis de ambiente do hospital.
- Em producao controlada, considere `RUN_MIGRATIONS_ON_STARTUP=false` e rode
	`poetry run alembic upgrade head` como etapa explicita de deploy.

# Gestão de acessos e recuperação de senha

O usuário mais antigo é promovido ao perfil `ti` pela migração
`20260622_009`. Esse perfil pode criar contas, bloquear acessos e definir
senhas temporárias pela interface administrativa.

Para habilitar o envio dos links de recuperação, configure na API:

```env
FRONTEND_BASE_URL=http://localhost:8080
FRONTEND_PASSWORD_RESET_URL=http://localhost:8080/autenticacao/redefinir-senha#token={token}
CORS_ALLOWED_ORIGINS=http://localhost:8080
SMTP_HOST=smtp.hostinger.com
SMTP_PORT=465
SMTP_USER=usuario_smtp@exemplo.local
SMTP_PASSWORD=senha_do_email
SMTP_FROM="TI Hospital Prontocardio <usuario_smtp@exemplo.local>"
SMTP_USE_SSL=true
SMTP_USE_TLS=false
```
