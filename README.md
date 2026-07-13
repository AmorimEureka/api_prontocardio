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

## Ciclo da tela Conciliação (Fiscal X Faturamento)

A tela de **Conciliacao (Fiscal X Faturamento)** relaciona uma NFS-e a uma ou
mais remessas do mesmo convenio. A pesquisa e o salvamento executam a mesma
regra de elegibilidade, de forma que uma chamada direta ao endpoint nao
consegue contornar as validacoes exibidas na interface.

```mermaid
flowchart TD
    A["Selecionar uma NFS-e pendente"] --> B{"Convenio da NFS-e foi localizado?"}
    B -- "Nao" --> BX["Bloquear: convenio nao identificado"]
    B -- "Sim" --> C["Pesquisar remessas no Oracle pelo CNPJ do convenio"]
    C --> D{"Remessa pertence ao mesmo convenio?"}
    D -- "Nao" --> DX["Excluir a remessa do resultado"]
    D -- "Sim" --> E{"Existe conciliacao anterior para a remessa?"}

    E -- "Nao" --> F{"Existe apenas acato historico?"}
    F -- "Sim" --> FX["Bloquear: acato e perda reconhecida e nao gera NFS-e"]
    F -- "Nao" --> G{"Existe recurso historico ativo, sem pagamento e nao consumido?"}
    G -- "Nao" --> H["Fluxo inicial: considerar o valor original da remessa"]
    G -- "Sim" --> I["Considerar somente o recurso disponivel"]

    E -- "Sim" --> J{"Existe recurso ativo, sem pagamento e nao consumido?"}
    J -- "Nao" --> JX["Bloquear: conciliacao anterior sem novo recurso"]
    J -- "Sim" --> I
    I --> IR["Recebimento anterior pode estar ausente, parcial ou integral"]

    H --> N["Adicionar remessa a NFS-e"]
    N --> O{"GLOSA marcada?"}
    O -- "Nao" --> P{"Valor glosado esta zerado?"}
    P -- "Nao" --> PX["Bloquear: remessa sem glosa deve ter valor zero"]
    P -- "Sim" --> R["Calcular valor conciliado"]
    O -- "Sim" --> Q{"Valor glosado e maior que zero e nao supera o valor considerado?"}
    Q -- "Nao" --> QX["Bloquear: valor de glosa invalido"]
    Q -- "Sim" --> R

    R --> S{"Soma das remessas menos glosas e igual ao valor da NFS-e?"}
    S -- "Nao" --> SX["Bloquear: totais divergentes"]
    S -- "Sim" --> T["Revalidar convenio, conciliacao anterior, recurso e glosa no backend"]
    T --> U{"Revalidacao aprovada?"}
    U -- "Nao" --> UX["Cancelar a operacao e informar o motivo"]
    U -- "Sim" --> V["Registrar a conciliacao e consumir o recurso utilizado"]

    V --> W{"Data de recebimento foi informada?"}
    W -- "Nao" --> WA["Aguardar registro posterior do recebimento"]
    WA --> WB["Registrar recebimento por remessa e NFS-e"]
    W -- "Sim" --> X["Registrar valor recebido por remessa e NFS-e"]
    WB --> Y["Somar todos os recebimentos da remessa"]
    X --> Y
    Y --> YV{"Recebimentos superam o total da remessa menos os acatos?"}
    YV -- "Sim" --> ZX["Rejeitar: recebimento excede o saldo cobravel"]
    YV -- "Nao" --> Z{"Recebimentos sao iguais ao total da remessa?"}
    Z -- "Sim" --> ZA["Marcar recebimento integral"]
    Z -- "Nao" --> ZAA{"Recebimentos mais acatos encerram a remessa?"}
    ZAA -- "Sim" --> KAX["Encerrar financeiramente sem confundir acato com recebimento"]
    ZAA -- "Nao" --> ZB["Manter recebimento parcial e saldo em aberto"]
    ZB --> ZC{"Tratamento do saldo glosado"}
    ZC -- "Recursar" --> ZD["Cadastrar novo recurso ativo e sem pagamento"]
    ZD --> A
    ZC -- "Acatar" --> ZE["Registrar perda aceita e reduzir o saldo cobravel"]
    ZE --> ZF{"Recebimentos mais acatos encerram a remessa?"}
    ZF -- "Sim" --> KAX
    ZF -- "Nao" --> ZD
```

### Regras e formulas aplicadas

- Somente remessas cujo CNPJ normalizado seja igual ao CNPJ do convenio da
  NFS-e podem ser selecionadas.
- O acato e armazenado nos registros de glosa com `sn_glosado = "not"`. Por
  compatibilidade com o modelo existente, o valor fica na coluna
  `valor_recursado`, mas semanticamente representa `valor_acatado`.
- O saldo cobravel da remessa e calculado por:

  ```text
  saldo_cobravel = valor_total_remessa
                   - SUM(valor_recebido)
                   - SUM(valor_acatado)
  ```

- Uma remessa e integral quando a soma dos recebimentos e exatamente igual ao
  seu valor total. O acato nao altera `recebimento_integral`, porque nao houve
  entrada de dinheiro.
- A remessa fica encerrada financeiramente quando
  `SUM(valor_recebido) + SUM(valor_acatado) == valor_total_remessa`. Nesse caso,
  o saldo e zero, mas a API diferencia encerramento financeiro de recebimento
  integral pelos campos `remessa_encerrada_financeiramente` e
  `remessa_recebida_integralmente`.
- Na pesquisa de remessas elegiveis, a API tambem retorna
  `valor_total_acatado`, `saldo_cobravel`, `valor_elegivel_conciliacao` e
  `situacao_financeira`. O `saldo_cobravel` continua representando a posicao
  financeira global da remessa, enquanto `valor_elegivel_conciliacao` e o
  valor usado nos totais da nova NFS-e.
- O recebimento das conciliacoes anteriores nao participa da elegibilidade
  fiscal. Uma remessa com recurso disponivel pode ser conciliada novamente se
  o recebimento anterior estiver ausente, parcial ou integral.
- Para evitar duplicidade, uma remessa que ja possui conciliacao anterior e
  nao possui novo recurso disponivel permanece bloqueada. A API devolve uma
  `restricao` estruturada com o motivo `conciliacao_sem_recurso`.
- Recebimentos acima de `valor_total_remessa - SUM(valor_acatado)` sao
  rejeitados, pois tentariam receber uma parcela ja reconhecida como perda.
- Para uma remessa com saldo aberto, o recurso disponivel considera somente
  recursos ativos, marcados como glosados, com processo e data de recurso, sem
  qualquer pagamento e ainda nao consumidos por outra conciliacao:

  ```text
  recurso_disponivel = MIN(
      total_recursado - total_consumido_em_conciliacoes,
      total_recursado_sem_qualquer_pagamento
  )
  ```

- Um recurso com pagamento parcial nao e considerado recurso "sem pagamento".
  O saldo remanescente desse recurso nao libera uma nova NFS-e.
- Valores acatados nao entram em `recurso_disponivel`, nao geram nova NFS-e e
  nao podem possuir dados de recebimento.
- Nao e exigida igualdade entre `recurso_disponivel` e `saldo_cobravel`. Para
  uma conciliacao de recurso, vale:

  ```text
  valor_elegivel_conciliacao = recurso_disponivel
  ```

  O recurso e validado e consumido independentemente do calendario de
  recebimentos das NFS-e anteriores.
- Em uma glosa parcialmente acatada e parcialmente recursada, o acato reduz o
  saldo cobravel, mas somente a parcela recursada e ainda disponivel participa
  da nova conciliacao. O recebimento anterior nao precisa ocorrer antes desse
  novo ciclo fiscal.
- Para remessas historicas que possuem recurso aberto, mas ainda nao possuem
  conciliacao anterior no modulo, o recurso sem pagamento e usado como valor
  elegivel. A falta de controle financeiro historico nao bloqueia a operacao
  quando o recurso pode ser validado.
- Quando `GLOSA?` estiver marcada, o valor glosado deve ser maior que zero e
  menor ou igual ao valor considerado. Sem a marcacao, o valor deve ser zero.
- Para cada NFS-e, deve ser respeitada a igualdade:

  ```text
  SUM(valor_considerado_das_remessas) - SUM(valor_glosado) = valor_nfse
  ```

- Em uma conciliacao de recurso, uma nova glosa reduz o recebimento registrado.
  O saldo resultante pode ser recursado, exigindo um novo recurso elegivel, ou
  acatado, encerrando a parcela como perda sem gerar outra NFS-e.
- O recurso e consumido pela conciliacao fiscal mesmo quando o recebimento
  financeiro for registrado posteriormente. Isso impede que o mesmo recurso
  seja associado a duas NFS-e.
- O backend repete todas as validacoes no `POST`, incluindo convenio,
  conciliacao anterior, disponibilidade e consumo do recurso, glosa e total da
  NFS-e. O estado de recebimento e validado apenas nos endpoints financeiros.

### Fila de conciliacoes sem recebimento

O submenu **Financeiro > Conciliacoes sem recebimento** consulta o endpoint
`GET /app_glosas/financeiro/conciliacao-faturamento/sem-recebimento`.

- A verificacao e feita pelo par `conciliacao_id + cd_remessa` na tabela
  `recebimentos_remessas`; a data de recebimento existente no cabecalho da
  conciliacao nao e usada isoladamente para definir a situacao.
- Uma remessa entra na fila quando
  `valor_total - valor_glosado > 0` e nao existe recebimento vinculado ao mesmo
  par conciliacao/remessa.
- Conciliacoes integralmente glosadas nao entram na fila, pois nao possuem
  valor esperado de recebimento.
- Uma conciliacao sem qualquer valor recebido recebe a situacao
  `sem_recebimento`. Quando existe recebimento em pelo menos uma remessa e
  outra permanece pendente, recebe `recebimento_parcial`. Remessas sem valor
  esperado, por terem sido integralmente glosadas, nao alteram essa situacao.
- O valor pendente e calculado somente sobre as remessas sem recebimento:

  ```text
  valor_pendente = SUM(valor_total_remessa - valor_glosado)
  ```

- A tela permite pesquisar por NFS-e, convenio, processo de recebimento ou
  codigo de uma remessa ainda pendente, possui paginacao e destaca previsoes
  de recebimento em atraso.
- Quando todas as remessas com valor esperado possuem registro de recebimento,
  a conciliacao deixa automaticamente a fila.
- Cada remessa pendente possui um formulario para registrar o recebimento pelo
  endpoint `POST /app_glosas/financeiro/conciliacao-faturamento/recebimentos-remessas`.
  O registro exige data, valor recebido e conta bancaria, e permite informar
  conta do plano de contas, conta do centro de custo e lancamento do extrato.
- O valor e preenchido com mascara monetaria e nao pode superar o valor
  pendente exibido. A API repete a validacao considerando todos os recebimentos
  e acatos da remessa.
- Os lancamentos disponiveis sao filtrados pela conta bancaria e pela data do
  recebimento. Ao confirmar um recebimento vinculado ao extrato, o lancamento
  e marcado como conciliado na mesma transacao.
- Depois do registro, a fila e recalculada: a remessa recebida deixa a lista e,
  se ainda houver outra remessa pendente, a conciliacao permanece com situacao
  `recebimento_parcial`.

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
