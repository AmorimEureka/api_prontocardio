from http import HTTPStatus

from app_prontocardio.permissions import telas_padrao
from app_prontocardio.schema import UserPublic
from app_prontocardio.security import criar_token


def test_consultar_usuario_atual(cliente, usuario_teste, token_teste):
    response = cliente.get(
        '/usuarios/me',
        headers={'Authorization': f'Bearer {token_teste}'},
    )

    assert response.status_code == HTTPStatus.OK
    assert (
        response.json()
        == UserPublic.model_validate(usuario_teste).model_dump()
    )


def test_consultar_usuarios(cliente, usuario_teste, token_teste):
    """Testando consulta de usuários cadastrados"""

    usuario_autorizado = UserPublic.model_validate(usuario_teste).model_dump()

    response = cliente.get(
        '/usuarios/', headers={'Authorization': f'Bearer {token_teste}'}
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {'usuarios': [usuario_autorizado]}


def test_criar_usuario(cliente, token_teste):
    """Testando a criação de usuário"""

    response = cliente.post(
        '/usuarios/',
        headers={'Authorization': f'Bearer {token_teste}'},
        json={
            'nome': 'usuario_novo',
            'email': 'usuario_novo@teste.com',
            'senha': 'testes123',
        },
    )

    assert response.status_code == HTTPStatus.CREATED
    assert response.json() == {
        'id': 2,
        'nome': 'usuario_novo',
        'email': 'usuario_novo@teste.com',
        'perfil': 'usuario',
        'ativo': True,
        'telas_permitidas': telas_padrao(),
    }


def test_criar_usuario_com_mesmo_nome(cliente, usuario_teste, token_teste):
    """Testando a criação de usuários com mesmo nome"""

    response = cliente.post(
        '/usuarios/',
        headers={'Authorization': f'Bearer {token_teste}'},
        json={
            'nome': usuario_teste.nome,
            'email': 'novo_email@teste.com',
            'senha': 'testes123',
        },
    )

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json() == {
        'detail': 'Já temos um usuário cadastrado com o mesmo nome/email!',
    }


def test_criar_usuario_com_mesmo_email(cliente, usuario_teste, token_teste):
    """Testando a criação de usuários com mesmo email"""

    response = cliente.post(
        '/usuarios/',
        headers={'Authorization': f'Bearer {token_teste}'},
        json={
            'nome': 'nome_novo',
            'email': usuario_teste.email,
            'senha': 'testes123',
        },
    )

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json() == {
        'detail': 'Já temos um usuário cadastrado com o mesmo nome/email!',
    }


def test_alterar_usuario(cliente, usuario_teste, token_teste):
    """Testando alteração simples do usuario"""

    response = cliente.put(
        f'/usuarios/{usuario_teste.id}',
        headers={'Authorization': f'Bearer {token_teste}'},
        json={
            'nome': 'pixolia',
            'email': 'pixolia@gmail.com',
            'senha': 'pipi1234',
        },
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {
        'nome': 'pixolia',
        'email': 'pixolia@gmail.com',
        'id': usuario_teste.id,
        'perfil': 'ti',
        'ativo': True,
        'telas_permitidas': telas_padrao(),
    }


def test_alterar_usuario_ja_cadastrado(cliente, usuario_teste, token_teste):
    """Testando alteração de usuário com dados já existentes"""

    cliente.post(
        '/usuarios/',
        headers={'Authorization': f'Bearer {token_teste}'},
        json={
            'nome': 'marie',
            'email': 'marie@gmail.com',
            'senha': 'xerie_anri',
            'perfil': 'usuario',
        },
    )

    response = cliente.put(
        f'/usuarios/{usuario_teste.id}',
        headers={'Authorization': f'Bearer {token_teste}'},
        json={
            'nome': 'marie',
            'email': 'xerie@gmail.com',
            'senha': 'xerie_anri',
        },
    )

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json() == {'detail': 'Nome ou e-mail já cadastrado'}


def test_alterar_usuario_com_outro_usuario(
    cliente, outro_usuario_teste, token_teste
):
    """Testando alteração de usuário diferente do usuário logado"""

    response = cliente.put(
        f'/usuarios/{outro_usuario_teste.id}',
        headers={'Authorization': f'Bearer {token_teste}'},
        json={
            'nome': 'pixolia',
            'email': 'pixolia@gmail.com',
            'senha': 'pipi1234',
        },
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json() == {'detail': 'Usuário sem permissão!!'}


def test_deletar_usuario(cliente, usuario_teste, token_teste):
    """Testando exclusão simples do usuário logado"""

    response = cliente.delete(
        f'/usuarios/{usuario_teste.id}',
        headers={'Authorization': f'Bearer {token_teste}'},
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {'message': 'Usuário Excluído!'}


def test_deletar_usuario_com_outro_usuario(
    cliente, outro_usuario_teste, token_teste
):
    """Testando exclusão de usuário diferente do usuário logado"""

    response = cliente.delete(
        f'/usuarios/{outro_usuario_teste.id}',
        headers={'Authorization': f'Bearer {token_teste}'},
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json() == {'detail': 'Usuário sem permissão!!'}


def test_usuario_ti_define_telas_permitidas(
    cliente,
    outro_usuario_teste,
    token_teste,
):
    response = cliente.patch(
        f'/usuarios/{outro_usuario_teste.id}/permissoes',
        headers={'Authorization': f'Bearer {token_teste}'},
        json={
            'telas_permitidas': [
                'solicitar_nota',
                'indicadores',
                'solicitar_nota',
            ]
        },
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()['telas_permitidas'] == [
        'indicadores',
        'solicitar_nota',
    ]


def test_rejeita_tela_inexistente(
    cliente,
    outro_usuario_teste,
    token_teste,
):
    response = cliente.patch(
        f'/usuarios/{outro_usuario_teste.id}/permissoes',
        headers={'Authorization': f'Bearer {token_teste}'},
        json={'telas_permitidas': ['tela_inexistente']},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_usuario_comum_nao_altera_permissoes(
    cliente,
    session,
    outro_usuario_teste,
    token_teste,
):
    outro_usuario_teste.perfil = 'usuario'
    session.commit()
    token_usuario = criar_token({'sub': outro_usuario_teste.email})

    response = cliente.patch(
        f'/usuarios/{outro_usuario_teste.id}/permissoes',
        headers={'Authorization': f'Bearer {token_usuario}'},
        json={'telas_permitidas': ['indicadores']},
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json() == {'detail': 'Acesso restrito à equipe de TI.'}


def test_usuario_comum_nao_cadastra_novo_usuario(
    cliente,
    session,
    outro_usuario_teste,
):
    outro_usuario_teste.perfil = 'usuario'
    session.commit()
    token_usuario = criar_token({'sub': outro_usuario_teste.email})

    response = cliente.post(
        '/usuarios/',
        headers={'Authorization': f'Bearer {token_usuario}'},
        json={
            'nome': 'usuario_sem_permissao',
            'email': 'sem.permissao@teste.com',
            'senha': 'testes123',
            'telas_permitidas': ['indicadores'],
        },
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json() == {'detail': 'Acesso restrito à equipe de TI.'}
