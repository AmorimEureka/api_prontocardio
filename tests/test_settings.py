import pytest
from pydantic import ValidationError

from app_prontocardio.settings import Settings


def _settings_producao(**overrides):
    valores = {
        'ORACLE_DATABASE_URL': 'oracle+oracledb://teste:teste@oracle/teste',
        'DATABASE_URL': (
            'postgresql+psycopg://usuario:senha@db.oficial.local:5432/'
            'prontocardio_db'
        ),
        'POSTGRES_SCHEMA': 'api_prontocardio',
        'SECRET_KEY': 'segredo-de-teste',
        'ALGORITHM': 'HS256',
        'APP_ENV': 'production',
        'EXPECTED_POSTGRES_HOST': 'db.oficial.local',
        'EXPECTED_POSTGRES_DATABASE': 'prontocardio_db',
    }
    valores.update(overrides)
    return Settings(**valores)


def test_producao_aceita_somente_banco_declarado_como_oficial():
    settings = _settings_producao()

    assert settings.EXPECTED_POSTGRES_HOST == 'db.oficial.local'
    assert settings.EXPECTED_POSTGRES_DATABASE == 'prontocardio_db'


@pytest.mark.parametrize(
    ('database_url', 'mensagem'),
    [
        (
            'postgresql+psycopg://usuario:senha@railway.local/railway',
            'DATABASE_URL não aponta para o PostgreSQL oficial',
        ),
        (
            'postgresql+psycopg://usuario:senha@db.oficial.local/railway',
            'DATABASE_URL não aponta para o PostgreSQL oficial',
        ),
    ],
)
def test_producao_recusa_host_ou_banco_divergente(
    database_url,
    mensagem,
):
    with pytest.raises(ValidationError, match=mensagem):
        _settings_producao(DATABASE_URL=database_url)


def test_producao_exige_identidade_do_banco_oficial():
    with pytest.raises(
        ValidationError,
        match='EXPECTED_POSTGRES_HOST é obrigatório em produção',
    ):
        _settings_producao(EXPECTED_POSTGRES_HOST=None)
