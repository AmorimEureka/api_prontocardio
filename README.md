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

## Ciclo da tela Conciliação (Faturamento X Fiscal)

A tela foi invertida para representar o fluxo operacional correto: a
**remessa MV** e o registro principal e as **NFS-e emitidas** sao alocadas
dentro dela. O card permanece na lista enquanto houver valor nao conciliado,
inclusive quando esse saldo estiver classificado como glosa pendente.

```mermaid
flowchart TD
    A["Listar remessas da HPC_V_CONTA_ATENDIMENTO"] --> B["Exibir numero, competencia, convenio, valor, conciliado e nao conciliado"]
    B --> C["Expandir a remessa"]
    C --> D["Exibir processo unico e historico das NFS-e ja vinculadas"]
    D --> E["Pesquisar NFS-e do mesmo CNPJ com saldo fiscal"]
    E --> F{"Ha glosa anterior sem recurso?"}
    F -- "Sim" --> FX["Bloquear nova NFS-e e orientar o tratamento/recurso"]
    F -- "Nao" --> G["Adicionar uma ou mais NFS-e"]
    G --> H["Informar valor utilizado, previsao e recebimento por NFS-e"]
    H --> I{"Glosar nesta NFS-e?"}
    I -- "Sim" --> J["Informar o valor glosado da remessa nesta nota"]
    I -- "Nao" --> K["Valor glosado igual a zero"]
    J --> L["Validar saldos da remessa, do recurso e da NFS-e"]
    K --> L
    L --> M["Gravar um vinculo fiscal por NFS-e e um processo por remessa"]
    M --> N{"Data de recebimento informada?"}
    N -- "Sim" --> O["Validar conta, data e lancamento; registrar recebimento"]
    N -- "Nao" --> P["Manter a NFS-e dentro da remessa em Conciliações sem Recebimento"]
    M --> Q{"Existe glosa?"}
    Q -- "Sim" --> R["Criar itens em registros_glosa e notificar o setor de glosas"]
    Q -- "Nao" --> S["Atualizar o saldo da remessa"]
    R --> T{"Tratamento interno"}
    T -- "Acatar" --> U["Reconhecer a perda e reduzir o saldo financeiro"]
    T -- "Recursar" --> V["Liberar o valor recursado para uma futura NFS-e"]
    V --> E
    U --> W{"Remessa encerrada?"}
    S --> W
    O --> W
    P --> W
    W -- "Nao" --> B
    W -- "Sim" --> X["Remover o card da listagem principal"]
```

### Persistencia e relacionamentos

- `processos_conciliacao_remessa` guarda um unico
  `processo_recebimento` por remessa. Todas as NFS-e adicionadas ao mesmo
  card reutilizam esse processo.
- `conciliacoes_faturamento` continua sendo o registro fiscal e bancario da
  NFS-e. Por isso `data_previsao_recebimento`, `data_recebimento`, conta
  bancaria, plano de contas, centro de custo e lancamento do extrato sao
  independentes para cada nota.
- `conciliacoes_faturamento_remessas` representa a alocacao entre uma NFS-e
  e uma remessa. O campo `valor_alocado_nfse` informa quanto do saldo da nota
  foi usado nesse vinculo.
- A unicidade global de `nfse_row_hash` e `numero_nfse` foi removida. A
  mesma NFS-e pode pagar remessas distintas, desde que a soma das alocacoes nao
  ultrapasse o valor liquido da nota.
- Os registros antigos sao preservados. A migracao calcula
  `valor_alocado_nfse = valor_total - valor_glosado` e agrupa o historico
  existente por remessa.
- A origem dos itens em `registros_glosa` e classificada como `triagem` para
  os registros historicos sem vinculo fiscal e como `conciliacao` para os
  itens criados a partir de `conciliacoes_faturamento_remessas`. A restricao
  de integridade impede que um item vinculado seja classificado como origem
  da Triagem, sem apagar a procedencia quando o vinculo deixar de existir.
- Para manter o contrato consumido pelos Indicadores, a API fornece
  `status_tratativa` e `valor_indicador`. Registros historicos continuam
  usando o valor recursado ou acatado; uma glosa ainda nao tratada da
  conciliacao contabiliza apenas o saldo do vinculo, uma unica vez, mesmo
  quando possui varios itens analiticos.

### Saldos e validacoes

O saldo fiscal da NFS-e e compartilhado entre todas as remessas:

```text
saldo_nfse = valor_liquido_nfse
             - SUM(valor_alocado_nfse em todas as remessas)
```

A posicao exibida no card da remessa e calculada por:

```text
valor_conciliado = SUM(valor_alocado_nfse)
saldo_base = MAX(valor_total_remessa
                 - SUM(valor_alocado_nfse)
                 - SUM(valor_glosado)
                 - valor_acatado, 0)
glosa_pendente = MAX(SUM(valor_glosado)
                     - valor_recurso_consumido
                     - valor_acatado, 0)
valor_nao_conciliado = MAX(saldo_base, glosa_pendente)
```

- O valor alocado deve ser maior que zero e menor ou igual ao saldo atual da
  NFS-e.
- A soma de `valor_alocado_nfse + valor_glosado` da operacao nao pode
  ultrapassar o valor disponivel da remessa.
- A glosa nao consome saldo da NFS-e, pois representa um valor contestado e
  ainda nao recebido. Ela classifica uma parte do saldo nao conciliado e cria
  itens analiticos no follow-up, mas nao e somada novamente ao saldo da
  remessa.
- Uma glosa ainda nao tratada permanece no valor nao conciliado, mas nao fica
  disponivel para outra NFS-e ate possuir recurso. Eventual parcela livre
  continua disponivel separadamente.
- O acato reconhece a perda sem criar recebimento e reduz tanto a glosa
  pendente quanto o saldo da remessa.
- O recurso exige processo, data, quantidade e valor recursado em
  `registros_glosa`. Somente o saldo recursado ativo, sem pagamento e ainda
  nao consumido torna a parcela glosada disponivel para uma nova NFS-e, sem
  soma-la novamente ao valor nao conciliado.
- Quando uma conciliacao de recurso sofre nova glosa, a nova parcela volta ao
  follow-up e somente podera receber outra NFS-e depois de novo recurso.

### Recebimento bancario por NFS-e

- A data de previsao e obrigatoria em cada NFS-e adicionada.
- A data de recebimento e opcional. Quando informada, a conta bancaria da
  `DBAMV.HPC_V_CONTAS_BANCARIAS` torna-se obrigatoria; plano de contas,
  centro de custo e lancamento do extrato continuam opcionais.
- A data de recebimento nao pode ser futura.
- O valor do recebimento e exatamente o `valor_alocado_nfse`. A glosa nao
  integra o deposito bancario.
- Um lancamento do extrato, quando selecionado, precisa pertencer a conta e a
  data informadas e e marcado como conciliado na mesma transacao.

### Follow-up Conciliações sem Recebimento

O submenu apresenta um card por remessa e lista internamente cada NFS-e
conciliada cuja `data_recebimento` ainda nao foi informada. Totais, paginacao
e pesquisa tambem consideram remessas, sem duplicar o card quando houver mais
de uma nota pendente. A fila usa o vinculo exato
`conciliacao_id + cd_remessa` e permite preencher posteriormente data, conta
bancaria, plano de contas, centro de custo e lancamento.

Depois do registro, os dados bancarios sao atualizados em
`conciliacoes_faturamento`, o recebimento e gravado em
`recebimentos_remessas` e a NFS-e deixa o agrupamento. O card da remessa sai
da fila quando nao restar nenhuma nota pendente de recebimento.

Enquanto nao existir recebimento bancario, o follow-up tambem permite editar
o processo, a previsao, o valor recebido e o valor glosado de cada remessa ou
inativar a conciliacao. A alteracao dos valores respeita os saldos da remessa
e da NFS-e e nao permite modificar glosas que ja tenham tratamento ou recurso.
A inativacao e logica: libera os saldos da remessa e da NFS-e, preserva o
registro original e inativa os itens de glosa vinculados.

### Consulta e auditoria das conciliacoes

O submenu **Consultar conciliacoes** pesquisa por NFS-e, remessa, convenio,
CNPJ ou processo e permite filtrar conciliacoes recebidas, pendentes e
inativas. Os cards sao agrupados por remessa e apresentam internamente todas
as NFS-e vinculadas, seus valores, recebimentos bancarios e o historico de
operacoes. A paginacao e os totais tambem consideram remessas, evitando que
uma remessa com mais de uma nota seja contabilizada mais de uma vez.

Criacao, edicao, registro de recebimento e inativacao mantem o usuario e a
data da operacao. As alteracoes tambem sao registradas em
`auditorias_conciliacao_faturamento`, com os estados anterior e posterior.
Quando um vinculo entre a mesma NFS-e e remessa e inativado e posteriormente
recriado, a consulta reune os eventos dos registros em uma unica linha do
tempo e identifica os eventos originados no vinculo anterior.

### Cache e paginacao

As listagens financeiras usam o mesmo cache curto por rota e filtros da
Triagem, compartilhado entre os workers do frontend e invalidado apos cada
mutacao. A consulta de auditoria nao usa cache para refletir imediatamente as
operacoes. A pagina principal consulta 25 remessas por vez. No Oracle, total
e pagina sao obtidos na mesma varredura da `HPC_V_CONTA_ATENDIMENTO`. Apos
uma conciliacao, a API devolve a posicao atualizada da remessa para o frontend
atualizar o card e os totais ja carregados, evitando repetir imediatamente a
consulta completa ao Oracle.

### Endpoints do fluxo

- `GET /app_glosas/financeiro/conciliacao-faturamento/remessas`: cards das
  remessas ainda nao encerradas, com saldos e historico.
- `GET /app_glosas/financeiro/conciliacao-faturamento/remessas/{cd_remessa}/notas`:
  NFS-e do mesmo convenio com saldo disponivel.
- `POST /app_glosas/financeiro/conciliacao-faturamento/remessas/{cd_remessa}/conciliar`:
  grava o processo unico e uma ou mais alocacoes de NFS-e.
- `GET /app_glosas/financeiro/conciliacao-faturamento/sem-recebimento`:
  follow-up agrupado por remessa, com suas NFS-e sem recebimento bancario.
- `POST /app_glosas/financeiro/conciliacao-faturamento/recebimentos-remessas`:
  completa o recebimento da NFS-e pendente.
- `GET /app_glosas/financeiro/conciliacao-faturamento/conciliacoes`:
  pesquisa conciliacoes e retorna remessas, recebimentos, usuarios e auditoria.
- `PUT /app_glosas/financeiro/conciliacao-faturamento/conciliacoes/{id}`:
  edita uma conciliacao ainda sem recebimento.
- `DELETE /app_glosas/financeiro/conciliacao-faturamento/conciliacoes/{id}`:
  inativa logicamente uma conciliacao ainda sem recebimento.

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
ALGORITHM=HS25
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
