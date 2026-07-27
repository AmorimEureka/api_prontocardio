import factory
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app_prontocardio.app import app
from app_prontocardio.app import settings as app_settings
from app_prontocardio.database import get_session_postgres
from app_prontocardio.models import Usuario, table_registry
from app_prontocardio.security import gera_hash_senha

app_settings.RUN_MIGRATIONS_ON_STARTUP = False


@pytest.fixture
def session():
    """Sessão da engine para realizar transações"""

    engine = create_engine(
        'sqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )

    # Remove 'SCHEMA' do postgres para usar no sqlite
    for table in table_registry.metadata.tables.values():
        table.schema = None

    table_registry.metadata.create_all(engine)

    with Session(engine) as session:
        yield session

    table_registry.metadata.drop_all(engine)


# @pytest.fixture
# def cliente():

#     """Cliente de teste sem banco de dados"""

#     cliente = TestClient(app)
#     return cliente


@pytest.fixture
def cliente(session):
    """Configuranco cliente padrão dos testes"""

    def get_session_teste():
        return session

    app.dependency_overrides[get_session_postgres] = get_session_teste
    with TestClient(app) as cliente:
        yield cliente
    app.dependency_overrides.clear()


class UserFactory(factory.Factory):
    class Meta:
        model = Usuario

    nome = factory.sequence(lambda n: f'usuario_{n}')
    email = factory.LazyAttribute(lambda obj: f'{obj.nome}@teste.com')
    senha = factory.LazyAttribute(lambda obj: f'{obj.nome}_xpto')
    perfil = 'ti'


@pytest.fixture
def usuario_teste(session: Session):

    senha_teste = 'testes'
    usuario_db_test = UserFactory(senha=gera_hash_senha(senha_teste))

    session.add(usuario_db_test)
    session.commit()
    session.refresh(usuario_db_test)

    usuario_db_test.senha_limpa = senha_teste

    return usuario_db_test


@pytest.fixture
def outro_usuario_teste(session: Session):

    senha_teste = 'testes'
    usuario_db_test = UserFactory(senha=gera_hash_senha(senha_teste))

    session.add(usuario_db_test)
    session.commit()
    session.refresh(usuario_db_test)

    usuario_db_test.senha_limpa = senha_teste

    return usuario_db_test


@pytest.fixture
def token_teste(cliente, usuario_teste):

    response = cliente.post(
        '/autenticacao/token',
        data={
            'username': usuario_teste.email,
            'password': usuario_teste.senha_limpa,
        },
    )

    return response.json()['access_token']
