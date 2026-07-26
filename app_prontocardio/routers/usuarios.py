from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app_prontocardio.database import get_session_postgres
from app_prontocardio.models import Usuario
from app_prontocardio.schema import (
    FilterPage,
    Message,
    UserList,
    UserPasswordUpdate,
    UserPermissionsUpdate,
    UserPublic,
    UserSchema,
    UserStatusUpdate,
)
from app_prontocardio.security import (
    gera_hash_senha,
    valida_token_usuario_atual,
    valida_usuario_ti,
)

router = APIRouter(prefix='/usuarios', tags=['usuarios'])

SessionPostgres = Annotated[Session, Depends(get_session_postgres)]
Filter_Users = Annotated[FilterPage, Query()]
ValidaUsuarioAtual = Annotated[Usuario, Depends(valida_token_usuario_atual)]
ValidaUsuarioTi = Annotated[Usuario, Depends(valida_usuario_ti)]


@router.get('/me', status_code=HTTPStatus.OK, response_model=UserPublic)
def consultar_usuario_atual(usuario_atual: ValidaUsuarioAtual):
    return usuario_atual


@router.get('/', status_code=HTTPStatus.OK, response_model=UserList)
def consultar_usuario(
    filter_usuario: Filter_Users,
    usuario_atual: ValidaUsuarioTi,
    session: SessionPostgres,
):
    usuarios_banco = session.scalars(
        select(Usuario)
        .limit(filter_usuario.limit)
        .offset(filter_usuario.offset)
    ).all()

    return {'usuarios': usuarios_banco}


@router.post('/', status_code=HTTPStatus.CREATED, response_model=UserPublic)
def criar_usuario(
    usuario_input: UserSchema,
    usuario_atual: ValidaUsuarioTi,
    session: Session = Depends(get_session_postgres),
):
    """Verifica se usuário está cadastrado na API;
    Valida a senha-cru VS senha-hash"""

    usuario_banco = session.scalar(
        select(Usuario).where(
            (Usuario.nome == usuario_input.nome)
            | (Usuario.email == usuario_input.email)
        )
    )

    excessao_conflito_usuario = HTTPException(
        status_code=HTTPStatus.CONFLICT,
        detail='Já temos um usuário cadastrado com o mesmo nome/email!',
    )

    if usuario_banco:
        if usuario_banco.nome == usuario_input.nome:
            raise excessao_conflito_usuario
        elif usuario_banco.email == usuario_input.email:
            raise excessao_conflito_usuario

    senha_hasheada = gera_hash_senha(usuario_input.senha)

    usuario_banco = Usuario(
        **usuario_input.model_dump(exclude={'senha'}), senha=senha_hasheada
    )

    session.add(usuario_banco)
    session.commit()
    session.refresh(usuario_banco)

    return usuario_banco


@router.patch(
    '/{user_id}/status', status_code=HTTPStatus.OK, response_model=UserPublic
)
def alterar_status_usuario(
    user_id: int,
    status_input: UserStatusUpdate,
    usuario_atual: ValidaUsuarioTi,
    session: SessionPostgres,
):
    usuario = session.get(Usuario, user_id)
    if not usuario:
        raise HTTPException(HTTPStatus.NOT_FOUND, 'Usuário não encontrado.')
    if usuario.id == usuario_atual.id and not status_input.ativo:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST,
            'Você não pode desativar seu próprio acesso.',
        )
    usuario.ativo = status_input.ativo
    session.commit()
    session.refresh(usuario)
    return usuario


@router.patch(
    '/{user_id}/senha', status_code=HTTPStatus.OK, response_model=Message
)
def redefinir_senha_usuario(
    user_id: int,
    password_input: UserPasswordUpdate,
    usuario_atual: ValidaUsuarioTi,
    session: SessionPostgres,
):
    usuario = session.get(Usuario, user_id)
    if not usuario:
        raise HTTPException(HTTPStatus.NOT_FOUND, 'Usuário não encontrado.')
    usuario.senha = gera_hash_senha(password_input.senha)
    session.commit()
    return {'message': 'Senha temporária definida com sucesso.'}


@router.patch(
    '/{user_id}/permissoes',
    status_code=HTTPStatus.OK,
    response_model=UserPublic,
)
def alterar_permissoes_usuario(
    user_id: int,
    permissions_input: UserPermissionsUpdate,
    usuario_atual: ValidaUsuarioTi,
    session: SessionPostgres,
):
    usuario = session.get(Usuario, user_id)
    if not usuario:
        raise HTTPException(HTTPStatus.NOT_FOUND, 'Usuário não encontrado.')
    usuario.telas_permitidas = permissions_input.telas_permitidas
    session.commit()
    session.refresh(usuario)
    return usuario


@router.put('/{user_id}', status_code=HTTPStatus.OK, response_model=UserPublic)
def alterar_usuario(
    user_id: int,
    usuario_input: UserSchema,
    usuario_atual: ValidaUsuarioAtual,
    session: Session = Depends(get_session_postgres),
):

    if usuario_atual.id != user_id:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN, detail='Usuário sem permissão!!'
        )

    try:
        usuario_atual.nome = usuario_input.nome
        usuario_atual.email = usuario_input.email
        usuario_atual.senha = gera_hash_senha(usuario_input.senha)

        session.commit()
        session.refresh(usuario_atual)

        return usuario_atual

    except IntegrityError:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='Nome ou e-mail já cadastrado',
        )


@router.delete('/{user_id}', status_code=HTTPStatus.OK, response_model=Message)
def deletar_usuario(
    user_id: int,
    usuario_atual: ValidaUsuarioAtual,
    session: Session = Depends(get_session_postgres),
):

    if usuario_atual.id != user_id:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN, detail='Usuário sem permissão!!'
        )

    session.delete(usuario_atual)
    session.commit()

    return {'message': 'Usuário Excluído!'}
