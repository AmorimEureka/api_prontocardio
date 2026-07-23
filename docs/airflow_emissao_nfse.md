# Integração Airflow para emissão de NFS-e

O frontend não emite a nota diretamente. Ao solicitar uma emissão individual
ou em lote, a API:

1. valida que todas as solicitações estão com status `VALIDADA`;
2. cria um registro em `api_prontocardio.lote_emissao_nfse`;
3. cria um registro `PENDENTE` em `api_prontocardio.emissao_nfse` para cada
   solicitação;
4. altera o workflow para `EMISSAO_SOLICITADA`;
5. dispara a DAG configurada pela API REST do Airflow.

Se a configuração não estiver disponível, nenhum registro é retirado da fila.
Se o Airflow recusar ou não responder ao disparo, os itens retornam ao status
`VALIDADA`, permanecendo disponíveis para uma nova tentativa.

## Configuração da API

```env
AIRFLOW_NFSE_BASE_URL=https://airflow.exemplo.local
AIRFLOW_NFSE_DAG_ID=emissao_nfse
AIRFLOW_NFSE_DAG_RUNS_PATH=/api/v1/dags/{dag_id}/dagRuns
AIRFLOW_NFSE_TOKEN=
AIRFLOW_NFSE_USERNAME=
AIRFLOW_NFSE_PASSWORD=
AIRFLOW_NFSE_TIMEOUT_SECONDS=15
AIRFLOW_NFSE_VERIFY_SSL=true
```

Use `AIRFLOW_NFSE_TOKEN` para autenticação Bearer. Quando ele não estiver
preenchido, a integração usa autenticação básica caso
`AIRFLOW_NFSE_USERNAME` tenha sido informado. O caminho da API é configurável
para permitir versões diferentes do Airflow ou um gateway intermediário.

## Contrato do disparo

O `POST` para a API do Airflow envia:

```json
{
  "dag_run_id": "api_prontocardio_nfse_lote_42",
  "conf": {
    "origem": "API_PRONTOCARDIO",
    "lote_id": 42,
    "solicitacao_ids": [101, 102]
  }
}
```

O `dag_run_id` retornado pelo Airflow e a data do disparo ficam registrados no
lote para rastreabilidade.

## Consulta dos itens pela DAG

A DAG deve usar o `lote_id` recebido em `dag_run.conf` e consultar apenas os
itens ainda pendentes:

```sql
SELECT
    e.id AS emissao_id,
    e.lote_id,
    s.*
FROM api_prontocardio.emissao_nfse e
JOIN api_prontocardio.solicitacao_nota s
  ON s.id = e.solicitacao_nota_id
JOIN api_prontocardio.solicitacao_nota_workflow w
  ON w.solicitacao_nota_id = s.id
WHERE e.lote_id = :lote_id
  AND e.status = 'PENDENTE'
  AND w.status = 'EMISSAO_SOLICITADA';
```

Antes de processar um item, a DAG deve alterar `emissao_nfse.status` para
`PROCESSANDO`. Ao terminar:

- sucesso: preencher `numero_nfse` e `protocolo`, marcar a emissão e o
  workflow como `EMITIDA`;
- falha fiscal: preencher `erro`, marcar a emissão como `ERRO` e o workflow
  como `ERRO_EMISSAO`;
- ao concluir todos os itens, marcar o lote como `EMITIDA` se todas as
  emissões tiveram sucesso, ou `ERRO` caso exista alguma falha.

As atualizações da emissão, do workflow, do evento de auditoria e do lote
devem ocorrer na mesma transação. A DAG deve inserir em
`solicitacao_nota_evento` uma ação `NFSE_EMITIDA` ou `ERRO_EMISSAO`, usando o
usuário associado ao lote para preservar a rastreabilidade.

O processamento deve ser idempotente: uma execução repetida para o mesmo
`lote_id` não deve reemitir itens que já estejam `EMITIDA`.
