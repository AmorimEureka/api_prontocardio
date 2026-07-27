from types import SimpleNamespace

import httpx
import pytest

from app_prontocardio.services.airflow_nfse import (
    AirflowNfseIndisponivelError,
    AirflowNfseTriggerError,
    disparar_dag_emissao_nfse,
)


def _settings(**overrides):
    values = {
        'AIRFLOW_NFSE_BASE_URL': 'https://airflow.example.com',
        'AIRFLOW_NFSE_DAG_ID': 'emitir_nfse',
        'AIRFLOW_NFSE_DAG_RUNS_PATH': ('/api/v1/dags/{dag_id}/dagRuns'),
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
        cnpj_por_solicitacao={
            10: '05613278000158',
            20: '08711085000128',
        },
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
            'cnpj_por_solicitacao': {
                '10': '05613278000158',
                '20': '08711085000128',
            },
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


def test_disparo_airflow_usa_basic_auth_sem_token(monkeypatch):
    chamada = {}

    class Response:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {'dag_run_id': 'run-basic', 'state': 'queued'}

    def fake_post(_url, **kwargs):
        chamada.update(kwargs)
        return Response()

    monkeypatch.setattr(httpx, 'post', fake_post)

    disparar_dag_emissao_nfse(
        lote_id=42,
        solicitacao_ids=[10],
        settings=_settings(
            AIRFLOW_NFSE_TOKEN='',
            AIRFLOW_NFSE_USERNAME='admin',
            AIRFLOW_NFSE_PASSWORD='senha-local',
        ),
    )

    request = httpx.Request('POST', 'https://airflow.example.com')
    authenticated = next(chamada['auth'].auth_flow(request))
    assert authenticated.headers['Authorization'].startswith('Basic ')
    assert 'Authorization' not in chamada['headers']


def test_disparo_airflow_prioriza_bearer_sobre_basic(monkeypatch):
    chamada = {}

    class Response:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {'dag_run_id': 'run-bearer'}

    def fake_post(_url, **kwargs):
        chamada.update(kwargs)
        return Response()

    monkeypatch.setattr(httpx, 'post', fake_post)

    disparar_dag_emissao_nfse(
        lote_id=42,
        solicitacao_ids=[10],
        settings=_settings(
            AIRFLOW_NFSE_TOKEN='token-prioritario',
            AIRFLOW_NFSE_USERNAME='admin',
            AIRFLOW_NFSE_PASSWORD='senha-local',
        ),
    )

    assert chamada['headers']['Authorization'] == ('Bearer token-prioritario')
    assert chamada['auth'] is None


def test_disparo_airflow_timeout_nao_repete_post(monkeypatch):
    chamadas = 0

    def fake_post(*_args, **_kwargs):
        nonlocal chamadas
        chamadas += 1
        request = httpx.Request('POST', 'https://airflow.example.com')
        raise httpx.ReadTimeout('tempo excedido', request=request)

    monkeypatch.setattr(httpx, 'post', fake_post)

    with pytest.raises(AirflowNfseIndisponivelError):
        disparar_dag_emissao_nfse(
            lote_id=42,
            solicitacao_ids=[10],
            settings=_settings(),
        )

    assert chamadas == 1


@pytest.mark.parametrize(
    ('status_code', 'error_class'),
    [
        (401, AirflowNfseTriggerError),
        (500, AirflowNfseIndisponivelError),
    ],
)
def test_disparo_airflow_classifica_erro_http(
    monkeypatch,
    status_code,
    error_class,
):
    def fake_post(*_args, **_kwargs):
        request = httpx.Request('POST', 'https://airflow.example.com')
        return httpx.Response(status_code, request=request)

    monkeypatch.setattr(httpx, 'post', fake_post)

    with pytest.raises(error_class) as exc_info:
        disparar_dag_emissao_nfse(
            lote_id=42,
            solicitacao_ids=[10],
            settings=_settings(AIRFLOW_NFSE_PASSWORD='nao-expor'),
        )

    assert 'nao-expor' not in str(exc_info.value)


def test_conflito_de_dag_run_id_e_tratado_como_idempotente(monkeypatch):
    def fake_post(*_args, **_kwargs):
        request = httpx.Request('POST', 'https://airflow.example.com')
        return httpx.Response(409, request=request)

    monkeypatch.setattr(httpx, 'post', fake_post)

    response = disparar_dag_emissao_nfse(
        lote_id=42,
        solicitacao_ids=[10],
        settings=_settings(),
    )

    assert response.dag_run_id == 'api_prontocardio_nfse_lote_42'
    assert response.state is None
