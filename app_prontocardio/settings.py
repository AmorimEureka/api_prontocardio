from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SMTP_SSL_PORT = 465


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env', env_file_encoding='utf-8', extra='ignore'
    )

    ORACLE_DATABASE_URL: str
    DATABASE_URL: str | None = None
    POSTGRES_SCHEMA: str
    APP_ENV: str = 'development'
    EXPECTED_POSTGRES_HOST: str | None = None
    EXPECTED_POSTGRES_DATABASE: str | None = None
    RUN_MIGRATIONS_ON_STARTUP: bool = True
    SECRET_KEY: str
    ALGORITHM: str
    FRONTEND_BASE_URL: str = 'http://localhost:8080'
    FRONTEND_PASSWORD_RESET_URL: str | None = None
    CORS_ALLOWED_ORIGINS: str = ''
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM_EMAIL: str = 'nao-responda@prontocardio.com.br'
    SMTP_FROM: str | None = None
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool | None = None
    AIRFLOW_NFSE_BASE_URL: str | None = None
    AIRFLOW_NFSE_DAG_ID: str = 'emissao_nfse'
    AIRFLOW_NFSE_DAG_RUNS_PATH: str = (
        '/api/v1/dags/{dag_id}/dagRuns'
    )
    AIRFLOW_NFSE_TOKEN: str | None = None
    AIRFLOW_NFSE_USERNAME: str | None = None
    AIRFLOW_NFSE_PASSWORD: str | None = None
    AIRFLOW_NFSE_TIMEOUT_SECONDS: float = 15.0
    AIRFLOW_NFSE_VERIFY_SSL: bool = True

    @model_validator(mode='after')
    def validar_banco_de_producao(self):
        if self.APP_ENV.strip().casefold() not in {'prod', 'production'}:
            return self

        if not self.DATABASE_URL:
            raise ValueError('DATABASE_URL é obrigatória em produção.')
        if not self.EXPECTED_POSTGRES_HOST:
            raise ValueError(
                'EXPECTED_POSTGRES_HOST é obrigatório em produção.'
            )
        if not self.EXPECTED_POSTGRES_DATABASE:
            raise ValueError(
                'EXPECTED_POSTGRES_DATABASE é obrigatório em produção.'
            )

        destino = urlsplit(self.DATABASE_URL)
        host_atual = (destino.hostname or '').strip().casefold()
        host_esperado = self.EXPECTED_POSTGRES_HOST.strip().casefold()
        banco_atual = destino.path.lstrip('/').strip().casefold()
        banco_esperado = (
            self.EXPECTED_POSTGRES_DATABASE.strip().casefold()
        )
        if host_atual != host_esperado or banco_atual != banco_esperado:
            raise ValueError(
                'DATABASE_URL não aponta para o PostgreSQL oficial: '
                f'esperado {host_esperado}/{banco_esperado}, '
                f'recebido {host_atual}/{banco_atual}.'
            )

        return self

    @property
    def smtp_username(self) -> str | None:
        return self.SMTP_USERNAME or self.SMTP_USER

    @property
    def smtp_from_email(self) -> str:
        return self.SMTP_FROM or self.SMTP_FROM_EMAIL

    @property
    def smtp_use_ssl(self) -> bool:
        if self.SMTP_USE_SSL is not None:
            return self.SMTP_USE_SSL
        return self.SMTP_PORT == SMTP_SSL_PORT

    @property
    def smtp_use_tls(self) -> bool:
        return False if self.smtp_use_ssl else self.SMTP_USE_TLS

    @property
    def frontend_password_reset_url(self) -> str:
        if self.FRONTEND_PASSWORD_RESET_URL:
            url = self.FRONTEND_PASSWORD_RESET_URL.strip()
            if url:
                return url

        return (
            f'{self.FRONTEND_BASE_URL.rstrip("/")}'
            '/autenticacao/redefinir-senha'
        )

    @property
    def cors_allowed_origins(self) -> list[str]:
        origins = [
            origin.strip().rstrip('/')
            for origin in self.CORS_ALLOWED_ORIGINS.split(',')
            if origin.strip()
        ]
        if origins:
            return origins

        return [self.FRONTEND_BASE_URL.rstrip('/')]
