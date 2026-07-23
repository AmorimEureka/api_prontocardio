from dataclasses import dataclass

import httpx

from app_prontocardio.settings import Settings


class AirflowNfseNaoConfiguradoError(RuntimeError):
    pass


class AirflowNfseTriggerError(RuntimeError):
    pass


@dataclass(frozen=True)
class AirflowDagRun:
    dag_run_id: str
    state: str | None = None


def airflow_nfse_configurado(settings: Settings | None = None) -> bool:
    config = settings or Settings()
    return bool(
        str(config.AIRFLOW_NFSE_BASE_URL or '').strip()
        and str(config.AIRFLOW_NFSE_DAG_ID or '').strip()
    )


def disparar_dag_emissao_nfse(
    lote_id: int,
    solicitacao_ids: list[int],
    settings: Settings | None = None,
) -> AirflowDagRun:
    config = settings or Settings()
    if not airflow_nfse_configurado(config):
        raise AirflowNfseNaoConfiguradoError(
            'A integração com o Airflow para emissão de NFS-e '
            'ainda não está configurada.'
        )

    dag_id = config.AIRFLOW_NFSE_DAG_ID.strip()
    path = config.AIRFLOW_NFSE_DAG_RUNS_PATH.format(dag_id=dag_id)
    url = f'{config.AIRFLOW_NFSE_BASE_URL.rstrip("/")}/{path.lstrip("/")}'
    dag_run_id = f'api_prontocardio_nfse_lote_{lote_id}'
    headers = {'Accept': 'application/json'}
    auth = None
    if token := str(config.AIRFLOW_NFSE_TOKEN or '').strip():
        headers['Authorization'] = f'Bearer {token}'
    elif username := str(config.AIRFLOW_NFSE_USERNAME or '').strip():
        auth = httpx.BasicAuth(
            username,
            str(config.AIRFLOW_NFSE_PASSWORD or ''),
        )

    try:
        response = httpx.post(
            url,
            json={
                'dag_run_id': dag_run_id,
                'conf': {
                    'origem': 'API_PRONTOCARDIO',
                    'lote_id': lote_id,
                    'solicitacao_ids': solicitacao_ids,
                },
            },
            headers=headers,
            auth=auth,
            timeout=config.AIRFLOW_NFSE_TIMEOUT_SECONDS,
            verify=config.AIRFLOW_NFSE_VERIFY_SSL,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AirflowNfseTriggerError(
            'O Airflow não aceitou o disparo da emissão de NFS-e.'
        ) from exc

    returned_run_id = str(
        payload.get('dag_run_id') or dag_run_id
    ).strip()
    return AirflowDagRun(
        dag_run_id=returned_run_id,
        state=str(payload.get('state') or '').strip() or None,
    )
