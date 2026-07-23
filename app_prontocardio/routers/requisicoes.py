from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app_prontocardio.database import (
    get_session_oracle,
    get_session_postgres,
)
from app_prontocardio.models import (
    ModelContaAtendimento,
    ModelHpcPaciente,
    SolicitacaoNota,
    Usuario,
)
from app_prontocardio.schema import (
    AtendimentoSolicitacaoNotaPublic,
    SolicitacaoNotaCreate,
    SolicitacaoNotaList,
    SolicitacaoNotaPublic,
)
from app_prontocardio.security import valida_token_usuario_atual

router = APIRouter(
    prefix='/app_glosas/requisicoes',
    tags=['requisicoes'],
)

ValidaUsuarioAtual = Annotated[Usuario, Depends(valida_token_usuario_atual)]
SessionPostgres = Annotated[Session, Depends(get_session_postgres)]
SessionOracle = Annotated[Session, Depends(get_session_oracle)]


def _texto(value) -> str | None:
    texto = str(value or '').strip()
    return texto or None


def _consultar_atendimento(
    codigo_atendimento: int,
    session_oracle: Session,
) -> AtendimentoSolicitacaoNotaPublic:
    try:
        atendimento = session_oracle.execute(
            select(
                ModelContaAtendimento.cd_paciente,
                ModelContaAtendimento.nm_paciente,
                ModelContaAtendimento.cd_convenio,
                ModelContaAtendimento.nm_convenio,
                ModelContaAtendimento.tp_atendimento,
            )
            .where(
                ModelContaAtendimento.cd_atendimento
                == codigo_atendimento
            )
            .order_by(
                ModelContaAtendimento.dt_ordenacao.desc().nulls_last(),
                ModelContaAtendimento.cd_reg.desc(),
                ModelContaAtendimento.cd_lancamento.desc(),
            )
        ).first()
        if atendimento is None or atendimento.cd_paciente is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail='Código de atendimento não encontrado.',
            )

        paciente = session_oracle.scalar(
            select(ModelHpcPaciente).where(
                ModelHpcPaciente.cd_paciente == atendimento.cd_paciente
            )
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Não foi possível consultar os dados no Oracle.',
        ) from exc

    if paciente is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Paciente do atendimento não encontrado.',
        )

    convenio = _texto(atendimento.nm_convenio)
    if atendimento.cd_convenio is None or not convenio:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='O atendimento não possui convênio.',
        )

    nome_paciente = _texto(paciente.paciente) or _texto(
        atendimento.nm_paciente
    )
    if not nome_paciente:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='O atendimento não possui nome de paciente.',
        )

    return AtendimentoSolicitacaoNotaPublic(
        codigo_atendimento=codigo_atendimento,
        codigo_paciente=int(atendimento.cd_paciente),
        codigo_convenio=int(atendimento.cd_convenio),
        nm_paciente=nome_paciente,
        convenio=convenio,
        nr_cpf=_texto(paciente.cpf),
        nr_cep=_texto(paciente.cep),
        ds_endereco=_texto(paciente.rua),
        nr_endereco=_texto(paciente.numero_casa),
        nm_bairro=_texto(paciente.bairro),
        ds_complemento=_texto(paciente.complemento),
        email=_texto(paciente.email),
        nr_fone=_texto(paciente.contato),
        tipo_atendimento=(
            _texto(atendimento.tp_atendimento) or 'Não informado'
        ),
    )


@router.get(
    '/atendimentos/{codigo_atendimento}',
    status_code=HTTPStatus.OK,
    response_model=AtendimentoSolicitacaoNotaPublic,
)
def consultar_atendimento_solicitacao_nota(
    codigo_atendimento: int,
    usuario_atual: ValidaUsuarioAtual,
    session_oracle: SessionOracle,
):
    del usuario_atual
    return _consultar_atendimento(codigo_atendimento, session_oracle)


@router.get(
    '/solicitacoes-nota',
    status_code=HTTPStatus.OK,
    response_model=SolicitacaoNotaList,
)
def listar_solicitacoes_nota(
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    del usuario_atual
    total = session_postgres.scalar(
        select(func.count()).select_from(SolicitacaoNota)
    ) or 0
    solicitacoes = session_postgres.scalars(
        select(SolicitacaoNota)
        .order_by(
            SolicitacaoNota.data_criacao.desc(),
            SolicitacaoNota.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()
    return SolicitacaoNotaList(
        solicitacoes=[
            SolicitacaoNotaPublic.model_validate(solicitacao)
            for solicitacao in solicitacoes
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    '/solicitacoes-nota',
    status_code=HTTPStatus.CREATED,
    response_model=SolicitacaoNotaPublic,
)
def cadastrar_solicitacao_nota(
    payload: SolicitacaoNotaCreate,
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
    session_oracle: SessionOracle,
):
    atendimento = _consultar_atendimento(
        payload.codigo_atendimento,
        session_oracle,
    )
    solicitacao = SolicitacaoNota(
        codigo_atendimento=atendimento.codigo_atendimento,
        codigo_paciente=atendimento.codigo_paciente,
        codigo_convenio=atendimento.codigo_convenio,
        nm_paciente=atendimento.nm_paciente,
        convenio=atendimento.convenio,
        local=payload.local.value,
        procedimento=payload.procedimento,
        tipo_atendimento=atendimento.tipo_atendimento,
        usuario_id=usuario_atual.id,
        nr_cpf=atendimento.nr_cpf,
        nr_cep=atendimento.nr_cep,
        ds_endereco=atendimento.ds_endereco,
        nr_endereco=atendimento.nr_endereco,
        nm_bairro=atendimento.nm_bairro,
        ds_complemento=atendimento.ds_complemento,
        email=atendimento.email,
        nr_fone=atendimento.nr_fone,
    )
    try:
        session_postgres.add(solicitacao)
        session_postgres.commit()
        session_postgres.refresh(solicitacao)
    except SQLAlchemyError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Não foi possível cadastrar a solicitação da nota.',
        ) from exc
    return solicitacao
