from datetime import datetime
from http import HTTPStatus
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased

from app_prontocardio.database import (
    get_session_oracle,
    get_session_postgres,
)
from app_prontocardio.models import (
    DecisaoValidacaoSolicitacao,
    EmissaoNfse,
    LoteEmissaoNfse,
    ModelContaAtendimento,
    ModelHpcPaciente,
    SolicitacaoNota,
    SolicitacaoNotaEvento,
    SolicitacaoNotaWorkflow,
    StatusEmissaoNfse,
    StatusWorkflowSolicitacao,
    TipoLoteEmissaoNfse,
    Usuario,
)
from app_prontocardio.schema import (
    AtendimentoSolicitacaoNotaPublic,
    EmissaoNfseCreate,
    EmissaoNfsePublic,
    LoteEmissaoNfsePublic,
    SolicitacaoNotaCreate,
    SolicitacaoNotaList,
    SolicitacaoNotaPublic,
    SolicitacaoNotaWorkflowFilter,
    SolicitacaoNotaWorkflowList,
    SolicitacaoNotaWorkflowPublic,
    ValidacaoSolicitacaoNotaInput,
)
from app_prontocardio.security import valida_token_usuario_atual
from app_prontocardio.services.airflow_nfse import (
    AirflowNfseTriggerError,
    airflow_nfse_configurado,
    disparar_dag_emissao_nfse,
)

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


def _agora_local() -> datetime:
    return datetime.now(ZoneInfo('America/Sao_Paulo')).replace(tzinfo=None)


def _solicitacao_public(
    solicitacao: SolicitacaoNota,
    cadastrado_por: str | None = None,
) -> SolicitacaoNotaPublic:
    public = SolicitacaoNotaPublic.model_validate(solicitacao)
    return public.model_copy(
        update={'cadastrado_por': cadastrado_por},
    )


def _workflow_public(
    solicitacao: SolicitacaoNota,
    workflow: SolicitacaoNotaWorkflow,
    cadastrado_por: str | None = None,
    validado_por: str | None = None,
) -> SolicitacaoNotaWorkflowPublic:
    return SolicitacaoNotaWorkflowPublic(
        **_solicitacao_public(
            solicitacao,
            cadastrado_por,
        ).model_dump(),
        workflow_id=workflow.id,
        status=workflow.status,
        validacao=workflow.validacao,
        motivo_recusa=workflow.motivo_recusa,
        validado_por_id=workflow.validado_por_id,
        validado_por=validado_por,
        validado_em=workflow.validado_em,
        workflow_atualizado_em=workflow.data_atualizacao,
    )


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
    rows = session_postgres.execute(
        select(
            SolicitacaoNota,
            Usuario.nome.label('cadastrado_por'),
        )
        .join(Usuario, Usuario.id == SolicitacaoNota.usuario_id)
        .order_by(
            SolicitacaoNota.data_criacao.desc(),
            SolicitacaoNota.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()
    return SolicitacaoNotaList(
        solicitacoes=[
            _solicitacao_public(solicitacao, cadastrado_por)
            for solicitacao, cadastrado_por in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    '/solicitacoes-nota/workflow',
    status_code=HTTPStatus.OK,
    response_model=SolicitacaoNotaWorkflowList,
)
def listar_workflow_solicitacoes_nota(
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
    filtros_query: Annotated[SolicitacaoNotaWorkflowFilter, Query()],
):
    del usuario_atual
    filtros = [
        SolicitacaoNotaWorkflow.status == filtros_query.status.value,
    ]
    if nome_paciente := _texto(filtros_query.nome_paciente):
        filtros.append(
            SolicitacaoNota.nm_paciente.ilike(f'%{nome_paciente}%')
        )
    if cpf := _texto(filtros_query.cpf):
        filtros.append(SolicitacaoNota.nr_cpf.ilike(f'%{cpf}%'))
    if tipo_atendimento := _texto(filtros_query.tipo_atendimento):
        filtros.append(
            SolicitacaoNota.tipo_atendimento == tipo_atendimento
        )
    if local := _texto(filtros_query.local):
        filtros.append(SolicitacaoNota.local == local)

    criador = aliased(Usuario)
    validador = aliased(Usuario)
    base_query = (
        select(
            SolicitacaoNota,
            SolicitacaoNotaWorkflow,
            criador.nome.label('cadastrado_por'),
            validador.nome.label('validado_por'),
        )
        .join(
            SolicitacaoNotaWorkflow,
            SolicitacaoNotaWorkflow.solicitacao_nota_id
            == SolicitacaoNota.id,
        )
        .join(
            criador,
            criador.id == SolicitacaoNota.usuario_id,
        )
        .outerjoin(
            validador,
            validador.id == SolicitacaoNotaWorkflow.validado_por_id,
        )
        .where(*filtros)
    )
    total = session_postgres.scalar(
        select(func.count()).select_from(base_query.subquery())
    ) or 0
    rows = session_postgres.execute(
        base_query.order_by(
            SolicitacaoNotaWorkflow.data_atualizacao.desc(),
            SolicitacaoNota.id.desc(),
        )
        .limit(filtros_query.limit)
        .offset(filtros_query.offset)
    ).all()
    return SolicitacaoNotaWorkflowList(
        solicitacoes=[
            _workflow_public(
                solicitacao,
                workflow,
                cadastrado_por,
                validado_por,
            )
            for (
                solicitacao,
                workflow,
                cadastrado_por,
                validado_por,
            ) in rows
        ],
        total=total,
        limit=filtros_query.limit,
        offset=filtros_query.offset,
    )


@router.post(
    '/solicitacoes-nota/{solicitacao_id}/validacao',
    status_code=HTTPStatus.OK,
    response_model=SolicitacaoNotaWorkflowPublic,
)
def validar_solicitacao_nota(
    solicitacao_id: int,
    payload: ValidacaoSolicitacaoNotaInput,
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
):
    row = session_postgres.execute(
        select(
            SolicitacaoNota,
            SolicitacaoNotaWorkflow,
            Usuario.nome.label('cadastrado_por'),
        )
        .join(
            SolicitacaoNotaWorkflow,
            SolicitacaoNotaWorkflow.solicitacao_nota_id
            == SolicitacaoNota.id,
        )
        .join(Usuario, Usuario.id == SolicitacaoNota.usuario_id)
        .where(SolicitacaoNota.id == solicitacao_id)
        .with_for_update()
    ).first()
    if row is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Solicitação de nota não encontrada.',
        )
    solicitacao, workflow, cadastrado_por = row
    if workflow.status != StatusWorkflowSolicitacao.PENDENTE_VALIDACAO.value:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='A solicitação não está pendente de validação.',
        )
    if (
        payload.decisao == DecisaoValidacaoSolicitacao.VALIDADA
        and (solicitacao.valor_nota is None or solicitacao.valor_nota <= 0)
    ):
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='Informe o valor da nota antes de validar a solicitação.',
        )

    agora = _agora_local()
    workflow.status = payload.decisao.value
    workflow.validacao = payload.decisao.value
    workflow.motivo_recusa = payload.motivo_recusa
    workflow.validado_por_id = usuario_atual.id
    workflow.validado_em = agora
    workflow.data_atualizacao = agora
    evento = SolicitacaoNotaEvento(
        solicitacao_nota_id=solicitacao.id,
        usuario_id=usuario_atual.id,
        tipo_acao=payload.decisao.value,
        observacao=payload.motivo_recusa,
    )
    try:
        session_postgres.add(evento)
        session_postgres.commit()
        session_postgres.refresh(workflow)
    except SQLAlchemyError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Não foi possível registrar a validação.',
        ) from exc
    return _workflow_public(
        solicitacao,
        workflow,
        cadastrado_por=cadastrado_por,
        validado_por=usuario_atual.nome,
    )


@router.post(
    '/emissoes-nfse',
    status_code=HTTPStatus.ACCEPTED,
    response_model=LoteEmissaoNfsePublic,
)
def solicitar_emissao_nfse(
    payload: EmissaoNfseCreate,
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
):
    if not airflow_nfse_configurado():
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail=(
                'A integração com o Airflow para emissão de NFS-e '
                'ainda não está configurada.'
            ),
        )

    rows = session_postgres.execute(
        select(
            SolicitacaoNotaWorkflow,
            SolicitacaoNota.valor_nota,
        )
        .join(
            SolicitacaoNota,
            SolicitacaoNota.id
            == SolicitacaoNotaWorkflow.solicitacao_nota_id,
        )
        .where(
            SolicitacaoNotaWorkflow.solicitacao_nota_id.in_(
                payload.solicitacao_ids
            )
        )
        .with_for_update()
    ).all()
    workflows = {
        workflow.solicitacao_nota_id: workflow
        for workflow, _valor_nota in rows
    }
    valores = {
        workflow.solicitacao_nota_id: valor_nota
        for workflow, valor_nota in rows
    }
    indisponiveis = [
        solicitacao_id
        for solicitacao_id in payload.solicitacao_ids
        if (
            solicitacao_id not in workflows
            or workflows[solicitacao_id].status
            != StatusWorkflowSolicitacao.VALIDADA.value
            or valores.get(solicitacao_id) is None
            or valores[solicitacao_id] <= 0
        )
    ]
    if indisponiveis:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail=(
                'Somente solicitações validadas podem ser emitidas. '
                f'Verifique os registros: {indisponiveis}.'
            ),
        )

    tipo = (
        TipoLoteEmissaoNfse.LOTE
        if len(payload.solicitacao_ids) > 1
        else TipoLoteEmissaoNfse.INDIVIDUAL
    )
    lote = LoteEmissaoNfse(
        tipo=tipo.value,
        usuario_id=usuario_atual.id,
        status=StatusEmissaoNfse.PENDENTE.value,
    )
    agora = _agora_local()
    try:
        session_postgres.add(lote)
        session_postgres.flush()
        emissoes = []
        for solicitacao_id in payload.solicitacao_ids:
            emissao = EmissaoNfse(
                solicitacao_nota_id=solicitacao_id,
                lote_id=lote.id,
                usuario_id=usuario_atual.id,
                status=StatusEmissaoNfse.PENDENTE.value,
            )
            emissoes.append(emissao)
            workflow = workflows[solicitacao_id]
            workflow.status = (
                StatusWorkflowSolicitacao.EMISSAO_SOLICITADA.value
            )
            workflow.data_atualizacao = agora
            session_postgres.add(
                SolicitacaoNotaEvento(
                    solicitacao_nota_id=solicitacao_id,
                    usuario_id=usuario_atual.id,
                    tipo_acao='EMISSAO_SOLICITADA',
                    observacao=f'Lote de emissão #{lote.id}.',
                )
            )
        session_postgres.add_all(emissoes)
        session_postgres.commit()
        for emissao in emissoes:
            session_postgres.refresh(emissao)
    except SQLAlchemyError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Não foi possível colocar as NFS-e na fila de emissão.',
        ) from exc

    try:
        dag_run = disparar_dag_emissao_nfse(
            lote.id,
            payload.solicitacao_ids,
        )
    except AirflowNfseTriggerError as exc:
        lote.status = StatusEmissaoNfse.ERRO.value
        lote.erro_disparo = str(exc)
        for emissao in emissoes:
            emissao.status = StatusEmissaoNfse.ERRO.value
            emissao.erro = str(exc)
            workflow = workflows[emissao.solicitacao_nota_id]
            workflow.status = StatusWorkflowSolicitacao.VALIDADA.value
            workflow.data_atualizacao = _agora_local()
            session_postgres.add(
                SolicitacaoNotaEvento(
                    solicitacao_nota_id=emissao.solicitacao_nota_id,
                    usuario_id=usuario_atual.id,
                    tipo_acao='ERRO_DISPARO_EMISSAO',
                    observacao=str(exc),
                )
            )
        session_postgres.commit()
        raise HTTPException(
            status_code=HTTPStatus.BAD_GATEWAY,
            detail=(
                'Não foi possível acionar o Airflow. As solicitações '
                'continuam disponíveis para uma nova tentativa.'
            ),
        ) from exc

    lote.status = StatusEmissaoNfse.PROCESSANDO.value
    lote.dag_run_id = dag_run.dag_run_id
    lote.airflow_disparado_em = _agora_local()
    lote.erro_disparo = None
    session_postgres.commit()

    return LoteEmissaoNfsePublic(
        lote_id=lote.id,
        tipo=lote.tipo,
        status=lote.status,
        quantidade=len(emissoes),
        dag_run_id=lote.dag_run_id,
        emissoes=[
            EmissaoNfsePublic.model_validate(emissao)
            for emissao in emissoes
        ],
        message=(
            'Solicitação registrada e DAG do Airflow acionada. '
            f'Execução: {lote.dag_run_id}.'
        ),
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
        valor_nota=payload.valor_nota,
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
        session_postgres.flush()
        session_postgres.add(
            SolicitacaoNotaWorkflow(
                solicitacao_nota_id=solicitacao.id,
                status=(
                    StatusWorkflowSolicitacao.PENDENTE_VALIDACAO.value
                ),
            )
        )
        session_postgres.add(
            SolicitacaoNotaEvento(
                solicitacao_nota_id=solicitacao.id,
                usuario_id=usuario_atual.id,
                tipo_acao='CRIACAO',
                observacao=(
                    'Solicitação cadastrada com valor de '
                    f'R$ {payload.valor_nota:.2f}.'
                ),
            )
        )
        session_postgres.commit()
        session_postgres.refresh(solicitacao)
    except SQLAlchemyError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Não foi possível cadastrar a solicitação da nota.',
        ) from exc
    return _solicitacao_public(solicitacao, usuario_atual.nome)
