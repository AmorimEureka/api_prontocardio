from types import SimpleNamespace

import httpx
import pytest

from app_prontocardio.services.airflow_nfse import (
    AirflowNfseTriggerError,
    disparar_dag_emissao_nfse,
)


def _settings(**overrides):
    values = {
        'AIRFLOW_NFSE_BASE_URL': 'https://airflow.example.com',
        'AIRFLOW_NFSE_DAG_ID': 'emitir_nfse',
        'AIRFLOW_NFSE_DAG_RUNS_PATH': (
            '/api/v1/dags/{dag_id}/dagRuns'
        ),
        'AIRFLOW_NFSE_TOKEN': 'token-teste',
        'AIRFLOW_NFSE_USERNAME': None,
        'AIRFLOW_NFSE_PASSWORD': None,
        'AIRFLOW_NFSE_TIMEOUT_SECONDS': 15.0,
        'AIRFLOW_NFSE_VERIFY_SSL': True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_disparo_airflow_envia_lote_e_solicitacoes(monkeypatch):
    chamada = {}

    class Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                'dag_run_id': 'run-retornado',
                'state': 'queued',
            }

    def fake_post(url, **kwargs):
        chamada['url'] = url
        chamada.update(kwargs)
        return Response()

    monkeypatch.setattr(httpx, 'post', fake_post)

    response = disparar_dag_emissao_nfse(
        lote_id=42,
        solicitacao_ids=[10, 20],
        settings=_settings(),
    )

    assert chamada['url'] == (
        'https://airflow.example.com/api/v1/dags/emitir_nfse/dagRuns'
    )
    assert chamada['headers']['Authorization'] == 'Bearer token-teste'
    assert chamada['json'] == {
        'dag_run_id': 'api_prontocardio_nfse_lote_42',
        'conf': {
            'origem': 'API_PRONTOCARDIO',
            'lote_id': 42,
            'solicitacao_ids': [10, 20],
        },
    }
    assert response.dag_run_id == 'run-retornado'
    assert response.state == 'queued'


def test_disparo_airflow_converte_falha_http_em_erro_de_integracao(
    monkeypatch,
):
    def fake_post(*_args, **_kwargs):
        raise httpx.ConnectError('indisponível')

    monkeypatch.setattr(httpx, 'post', fake_post)

    with pytest.raises(AirflowNfseTriggerError):
        disparar_dag_emissao_nfse(
            lote_id=42,
            solicitacao_ids=[10],
            settings=_settings(),
        )
