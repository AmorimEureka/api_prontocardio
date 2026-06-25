from app_prontocardio.routers import autenticacao
from app_prontocardio.settings import Settings

HOSTINGER_SMTP_PORT = 465
SMTP_TIMEOUT_SECONDS = 10


def _settings_teste(**kwargs):
    return Settings(
        ORACLE_DATABASE_URL='oracle+oracledb://usuario:senha@host:1521/db',
        POSTGRES_SCHEMA='api_prontocardio_test',
        SECRET_KEY='secret-test',
        ALGORITHM='HS256',
        **kwargs,
    )


def test_configuracao_smtp_hostinger_usa_ssl_e_aliases():
    settings = _settings_teste(
        SMTP_HOST='smtp.hostinger.com',
        SMTP_PORT=465,
        SMTP_USER='usuario_smtp@exemplo.local',
        SMTP_PASSWORD='senha_do_email',
        SMTP_FROM='TI Hospital Prontocardio <usuario_smtp@exemplo.local>',
    )

    assert settings.smtp_username == 'usuario_smtp@exemplo.local'
    assert settings.smtp_from_email.startswith('TI Hospital Prontocardio')
    assert settings.smtp_use_ssl is True
    assert settings.smtp_use_tls is False


def test_link_redefinicao_usa_url_explicitada_do_frontend(monkeypatch):
    settings = _settings_teste(
        FRONTEND_BASE_URL='https://api.exemplo.local',
        FRONTEND_PASSWORD_RESET_URL=(
            'https://app.exemplo.local'
            '/autenticacao/redefinir-senha#token={token}'
        ),
    )

    monkeypatch.setattr(autenticacao, 'settings', settings)

    assert autenticacao._montar_link_redefinicao('abc_123') == (
        'https://app.exemplo.local'
        '/autenticacao/redefinir-senha#token=abc_123'
    )


def test_envio_redefinicao_usa_smtp_ssl_com_hostinger(monkeypatch):
    settings = _settings_teste(
        FRONTEND_BASE_URL='https://api.exemplo.local',
        FRONTEND_PASSWORD_RESET_URL=(
            'https://app.exemplo.local'
            '/autenticacao/redefinir-senha#token={token}'
        ),
        SMTP_HOST='smtp.hostinger.com',
        SMTP_PORT=465,
        SMTP_USER='usuario_smtp@exemplo.local',
        SMTP_PASSWORD='senha_do_email',
        SMTP_FROM='TI Hospital Prontocardio <usuario_smtp@exemplo.local>',
    )
    smtp_instances = []

    class SMTPSSLTeste:
        def __init__(self, host, port, timeout):
            self.host = host
            self.port = port
            self.timeout = timeout
            self.starttls_chamado = False
            self.login_args = None
            self.mensagem = None
            smtp_instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def starttls(self):
            self.starttls_chamado = True

        def login(self, username, password):
            self.login_args = (username, password)

        def send_message(self, mensagem):
            self.mensagem = mensagem

    def smtp_starttls_inesperado(*args, **kwargs):
        raise AssertionError('SMTP sem SSL nao deveria ser usado')

    monkeypatch.setattr(autenticacao, 'settings', settings)
    monkeypatch.setattr(autenticacao.smtplib, 'SMTP_SSL', SMTPSSLTeste)
    monkeypatch.setattr(autenticacao.smtplib, 'SMTP', smtp_starttls_inesperado)

    autenticacao._enviar_email_redefinicao('usuario@teste.com', 'token')

    smtp = smtp_instances[0]
    assert smtp.host == 'smtp.hostinger.com'
    assert smtp.port == HOSTINGER_SMTP_PORT
    assert smtp.timeout == SMTP_TIMEOUT_SECONDS
    assert smtp.starttls_chamado is False
    assert smtp.login_args == (
        'usuario_smtp@exemplo.local',
        'senha_do_email',
    )
    assert smtp.mensagem['From'].startswith('TI Hospital Prontocardio')
    assert smtp.mensagem['To'] == 'usuario@teste.com'

    conteudo = smtp.mensagem.get_content()
    assert (
        'https://app.exemplo.local'
        '/autenticacao/redefinir-senha#token=token'
    ) in conteudo
    assert '?token=' not in conteudo
    assert 'https://api.exemplo.local' not in conteudo
