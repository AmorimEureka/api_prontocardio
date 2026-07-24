import hashlib
from datetime import datetime
from http import HTTPStatus
from typing import Annotated
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, aliased

from app_prontocardio.database import (
    get_session_oracle,
    get_session_postgres,
)
from app_prontocardio.models import (
    DecisaoValidacaoSolicitacao,
    EmissaoNfse,
    EmissaoNfseArquivo,
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
    ProcedimentoAtendimentoPublic,
    SolicitacaoNotaCreate,
    SolicitacaoNotaEmissaoFilter,
    SolicitacaoNotaEmissaoList,
    SolicitacaoNotaEmissaoPublic,
    SolicitacaoNotaFilter,
    SolicitacaoNotaList,
    SolicitacaoNotaPublic,
    SolicitacaoNotaResumoStatus,
    SolicitacaoNotaUpdate,
    SolicitacaoNotaWorkflowFilter,
    SolicitacaoNotaWorkflowList,
    SolicitacaoNotaWorkflowPublic,
    ValidacaoSolicitacaoNotaInput,
)
from app_prontocardio.security import valida_token_usuario_atual
from app_prontocardio.services.airflow_nfse import (
    AirflowNfseIndisponivelError,
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
    status: str | None = None,
) -> SolicitacaoNotaPublic:
    public = SolicitacaoNotaPublic.model_validate(solicitacao)
    return public.model_copy(
        update={
            'cadastrado_por': cadastrado_por,
            'status': status,
        },
    )


def _workflow_public(
    solicitacao: SolicitacaoNota,
    workflow: SolicitacaoNotaWorkflow,
    cadastrado_por: str | None = None,
    validado_por: str | None = None,
    inativacao: tuple[int | None, str | None, datetime | None] | None = None,
) -> SolicitacaoNotaWorkflowPublic:
    inativado_por_id, inativado_por, inativado_em = inativacao or (
        None,
        None,
        None,
    )
    return SolicitacaoNotaWorkflowPublic(
        **_solicitacao_public(
            solicitacao,
            cadastrado_por,
            workflow.status,
        ).model_dump(),
        workflow_id=workflow.id,
        validacao=workflow.validacao,
        motivo_recusa=workflow.motivo_recusa,
        validado_por_id=workflow.validado_por_id,
        validado_por=validado_por,
        validado_em=workflow.validado_em,
        inativado_por_id=inativado_por_id,
        inativado_por=inativado_por,
        inativado_em=inativado_em,
        workflow_atualizado_em=workflow.data_atualizacao,
    )


def _lote_emissao_public(
    lote: LoteEmissaoNfse,
    emissoes: list[EmissaoNfse],
    message: str | None = None,
) -> LoteEmissaoNfsePublic:
    return LoteEmissaoNfsePublic(
        lote_id=lote.id,
        tipo=lote.tipo,
        status=lote.status,
        quantidade=len(emissoes),
        dag_run_id=lote.dag_run_id,
        airflow_disparado_em=lote.airflow_disparado_em,
        erro_disparo=lote.erro_disparo,
        data_criacao=lote.data_criacao,
        emissoes=[
            EmissaoNfsePublic.model_validate(emissao)
            for emissao in emissoes
        ],
        message=message,
    )


def _solicitacao_emissao_public(
    solicitacao: SolicitacaoNota,
    workflow: SolicitacaoNotaWorkflow,
    usuarios: tuple[str | None, str | None],
    emissao: EmissaoNfse | None,
    arquivo_disponivel: bool,
) -> SolicitacaoNotaEmissaoPublic:
    cadastrado_por, validado_por = usuarios
    return SolicitacaoNotaEmissaoPublic(
        **_workflow_public(
            solicitacao,
            workflow,
            cadastrado_por,
            validado_por,
        ).model_dump(),
        emissao_id=emissao.id if emissao else None,
        lote_id=emissao.lote_id if emissao else None,
        status_emissao=emissao.status if emissao else None,
        numero_nfse=emissao.numero_nfse if emissao else None,
        protocolo=emissao.protocolo if emissao else None,
        erro_emissao=emissao.erro if emissao else None,
        emissao_criada_em=emissao.data_criacao if emissao else None,
        emissao_atualizada_em=(
            emissao.data_atualizacao if emissao else None
        ),
        arquivo_disponivel=arquivo_disponivel,
    )


def _ultima_emissao_subquery():
    return (
        select(
            EmissaoNfse.solicitacao_nota_id.label(
                'solicitacao_nota_id'
            ),
            func.max(EmissaoNfse.id).label('emissao_id'),
        )
        .group_by(EmissaoNfse.solicitacao_nota_id)
        .subquery()
    )


def _ultima_inativacao_subquery():
    return (
        select(
            SolicitacaoNotaEvento.solicitacao_nota_id.label(
                'solicitacao_nota_id'
            ),
            func.max(SolicitacaoNotaEvento.id).label('evento_id'),
        )
        .where(SolicitacaoNotaEvento.tipo_acao == 'INATIVACAO')
        .group_by(SolicitacaoNotaEvento.solicitacao_nota_id)
        .subquery()
    )


def _consultar_procedimentos_atendimentos(
    codigos_atendimento: set[int],
    session_oracle: Session,
) -> tuple[dict[int, list[ProcedimentoAtendimentoPublic]], bool]:
    procedimentos_por_atendimento = {
        codigo: [] for codigo in codigos_atendimento
    }
    if not codigos_atendimento:
        return procedimentos_por_atendimento, True

    try:
        rows = session_oracle.execute(
            select(
                ModelContaAtendimento.cd_atendimento,
                ModelContaAtendimento.cd_pro_fat,
                ModelContaAtendimento.descricao,
                ModelContaAtendimento.ds_gru_fat,
                ModelContaAtendimento.qt_lancamento,
                ModelContaAtendimento.dt_lancamento,
                ModelContaAtendimento.nm_prestador,
            )
            .where(
                ModelContaAtendimento.cd_atendimento.in_(
                    codigos_atendimento
                )
            )
            .order_by(
                ModelContaAtendimento.cd_atendimento,
                ModelContaAtendimento.dt_ordenacao,
                ModelContaAtendimento.cd_reg,
                ModelContaAtendimento.cd_lancamento,
            )
        ).all()
    except SQLAlchemyError:
        return procedimentos_por_atendimento, False

    for row in rows:
        if row.cd_atendimento is None:
            continue
        codigo = _texto(row.cd_pro_fat)
        descricao = _texto(row.descricao)
        if not codigo and not descricao:
            continue
        procedimentos_por_atendimento.setdefault(
            int(row.cd_atendimento),
            [],
        ).append(
            ProcedimentoAtendimentoPublic(
                codigo=codigo,
                descricao=descricao or f'Procedimento {codigo}',
                grupo=_texto(row.ds_gru_fat),
                quantidade=row.qt_lancamento,
                realizado_em=row.dt_lancamento,
                prestador=_texto(row.nm_prestador),
            )
        )

    return procedimentos_por_atendimento, True


def _possui_dados_fiscais_obrigatorios(
    solicitacao: SolicitacaoNota,
) -> bool:
    return all(
        _texto(value)
        for value in (
            solicitacao.nr_cpf,
            solicitacao.nm_paciente,
            solicitacao.procedimento,
            solicitacao.local,
            solicitacao.tipo_atendimento,
        )
    )


def _carregar_workflows_para_emissao(
    solicitacao_ids: list[int],
    session_postgres: Session,
) -> dict[int, SolicitacaoNotaWorkflow]:
    rows = session_postgres.execute(
        select(
            SolicitacaoNota,
            SolicitacaoNotaWorkflow,
        )
        .join(
            SolicitacaoNotaWorkflow,
            SolicitacaoNota.id
            == SolicitacaoNotaWorkflow.solicitacao_nota_id,
        )
        .where(
            SolicitacaoNota.id.in_(solicitacao_ids),
            SolicitacaoNota.ativo.is_(True),
        )
        .with_for_update()
    ).all()
    solicitacoes = {
        solicitacao.id: solicitacao for solicitacao, _workflow in rows
    }
    workflows = {
        solicitacao.id: workflow for solicitacao, workflow in rows
    }
    estados_ativos = (
        StatusEmissaoNfse.PENDENTE.value,
        StatusEmissaoNfse.PROCESSANDO.value,
        StatusEmissaoNfse.EMITIDA.value,
    )
    emissoes_existentes = set(
        session_postgres.scalars(
            select(EmissaoNfse.solicitacao_nota_id)
            .where(
                EmissaoNfse.solicitacao_nota_id.in_(solicitacao_ids),
                EmissaoNfse.status.in_(estados_ativos),
            )
            .with_for_update()
        ).all()
    )
    indisponiveis = [
        solicitacao_id
        for solicitacao_id in solicitacao_ids
        if (
            solicitacao_id not in solicitacoes
            or solicitacao_id in emissoes_existentes
            or workflows[solicitacao_id].status
            != StatusWorkflowSolicitacao.VALIDADA.value
            or workflows[solicitacao_id].validacao
            != StatusWorkflowSolicitacao.VALIDADA.value
            or solicitacoes[solicitacao_id].valor_nota is None
            or solicitacoes[solicitacao_id].valor_nota <= 0
            or not _possui_dados_fiscais_obrigatorios(
                solicitacoes[solicitacao_id]
            )
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
    return workflows


def _preparar_lote_emissao(
    solicitacao_ids: list[int],
    workflows: dict[int, SolicitacaoNotaWorkflow],
    usuario_atual: Usuario,
    session_postgres: Session,
) -> tuple[LoteEmissaoNfse, list[EmissaoNfse]]:
    tipo = (
        TipoLoteEmissaoNfse.LOTE
        if len(solicitacao_ids) > 1
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
        for solicitacao_id in solicitacao_ids:
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
    except IntegrityError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail=(
                'Uma ou mais solicitações já possuem emissão ativa. '
                'Atualize a lista e tente novamente.'
            ),
        ) from exc
    except SQLAlchemyError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Não foi possível colocar as NFS-e na fila de emissão.',
        ) from exc
    return lote, emissoes


def _restaurar_apos_falha_airflow(
    lote: LoteEmissaoNfse,
    emissoes: list[EmissaoNfse],
    usuario_atual: Usuario,
    erro: AirflowNfseTriggerError,
    session_postgres: Session,
) -> None:
    erro_tecnico = str(erro)[:1000]
    workflows = {
        workflow.solicitacao_nota_id: workflow
        for workflow in session_postgres.scalars(
            select(SolicitacaoNotaWorkflow).where(
                SolicitacaoNotaWorkflow.solicitacao_nota_id.in_(
                    [
                        emissao.solicitacao_nota_id
                        for emissao in emissoes
                    ]
                )
            )
        ).all()
    }
    lote.status = StatusEmissaoNfse.ERRO.value
    lote.erro_disparo = erro_tecnico
    for emissao in emissoes:
        emissao.status = StatusEmissaoNfse.ERRO.value
        emissao.erro = erro_tecnico
        workflow = workflows[emissao.solicitacao_nota_id]
        workflow.status = StatusWorkflowSolicitacao.VALIDADA.value
        workflow.data_atualizacao = _agora_local()
        session_postgres.add(
            SolicitacaoNotaEvento(
                solicitacao_nota_id=emissao.solicitacao_nota_id,
                usuario_id=usuario_atual.id,
                tipo_acao='ERRO_DISPARO_EMISSAO',
                observacao=erro_tecnico[:500],
            )
        )
    try:
        session_postgres.commit()
    except SQLAlchemyError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail=(
                'Falha ao restaurar as solicitações após o erro '
                'de comunicação com o Airflow.'
            ),
        ) from exc


def _registrar_disparo_airflow(
    lote: LoteEmissaoNfse,
    emissoes: list[EmissaoNfse],
    dag_run_id: str,
    usuario_atual: Usuario,
    session_postgres: Session,
) -> None:
    lote.status = StatusEmissaoNfse.PROCESSANDO.value
    lote.dag_run_id = dag_run_id
    lote.airflow_disparado_em = _agora_local()
    lote.erro_disparo = None
    for emissao in emissoes:
        session_postgres.add(
            SolicitacaoNotaEvento(
                solicitacao_nota_id=emissao.solicitacao_nota_id,
                usuario_id=usuario_atual.id,
                tipo_acao='AIRFLOW_DISPARADO',
                observacao=(
                    f'Lote #{lote.id}; dag_run_id={dag_run_id}.'
                )[:500],
            )
        )
    try:
        session_postgres.commit()
    except SQLAlchemyError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail=(
                'O Airflow aceitou o lote, mas não foi possível '
                'persistir os dados do disparo.'
            ),
        ) from exc


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
    procedimentos, procedimentos_disponiveis = (
        _consultar_procedimentos_atendimentos(
            {codigo_atendimento},
            session_oracle,
        )
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
        procedimentos_atendimento=procedimentos.get(
            codigo_atendimento,
            [],
        ),
        procedimentos_atendimento_disponiveis=procedimentos_disponiveis,
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
    filtros_query: Annotated[SolicitacaoNotaFilter, Query()],
):
    del usuario_atual
    filtros_base = [SolicitacaoNota.ativo.is_(True)]
    if filtros_query.codigo_atendimento:
        filtros_base.append(
            SolicitacaoNota.codigo_atendimento
            == filtros_query.codigo_atendimento
        )
    if nome_paciente := _texto(filtros_query.nome_paciente):
        filtros_base.append(
            SolicitacaoNota.nm_paciente.ilike(f'%{nome_paciente}%')
        )
    if convenio := _texto(filtros_query.convenio):
        filtros_base.append(
            SolicitacaoNota.convenio.ilike(f'%{convenio}%')
        )
    if filtros_query.local:
        filtros_base.append(
            SolicitacaoNota.local == filtros_query.local.value
        )

    filtros = list(filtros_base)
    if filtros_query.status:
        filtros.append(
            SolicitacaoNotaWorkflow.status
            == filtros_query.status.value
        )

    ultima_emissao = _ultima_emissao_subquery()
    criador = aliased(Usuario)
    validador = aliased(Usuario)
    base_query = (
        select(
            SolicitacaoNota,
            SolicitacaoNotaWorkflow,
            criador.nome.label('cadastrado_por'),
            validador.nome.label('validado_por'),
            EmissaoNfse,
            EmissaoNfseArquivo.id.label('arquivo_id'),
        )
        .join(
            SolicitacaoNotaWorkflow,
            SolicitacaoNotaWorkflow.solicitacao_nota_id
            == SolicitacaoNota.id,
        )
        .join(criador, criador.id == SolicitacaoNota.usuario_id)
        .outerjoin(
            validador,
            validador.id == SolicitacaoNotaWorkflow.validado_por_id,
        )
        .outerjoin(
            ultima_emissao,
            ultima_emissao.c.solicitacao_nota_id == SolicitacaoNota.id,
        )
        .outerjoin(
            EmissaoNfse,
            EmissaoNfse.id == ultima_emissao.c.emissao_id,
        )
        .outerjoin(
            EmissaoNfseArquivo,
            EmissaoNfseArquivo.emissao_nfse_id == EmissaoNfse.id,
        )
        .where(*filtros)
    )
    total = session_postgres.scalar(
        select(func.count()).select_from(base_query.subquery())
    ) or 0

    resumo_rows = session_postgres.execute(
        select(
            SolicitacaoNotaWorkflow.status,
            func.count(SolicitacaoNota.id).label('quantidade'),
            func.coalesce(
                func.sum(SolicitacaoNota.valor_nota),
                0,
            ).label('valor_total'),
        )
        .join(
            SolicitacaoNotaWorkflow,
            SolicitacaoNotaWorkflow.solicitacao_nota_id
            == SolicitacaoNota.id,
        )
        .where(*filtros_base)
        .group_by(SolicitacaoNotaWorkflow.status)
    ).all()

    rows = session_postgres.execute(
        base_query.order_by(
            SolicitacaoNota.data_criacao.desc(),
            SolicitacaoNota.id.desc(),
        )
        .limit(filtros_query.limit)
        .offset(filtros_query.offset)
    ).all()

    solicitacoes = []
    for (
        solicitacao,
        workflow,
        cadastrado_por,
        validado_por,
        emissao,
        arquivo_id,
    ) in rows:
        arquivo_disponivel = bool(
            emissao
            and emissao.status == StatusEmissaoNfse.EMITIDA.value
            and arquivo_id is not None
        )
        solicitacoes.append(
            _solicitacao_emissao_public(
                solicitacao,
                workflow,
                (cadastrado_por, validado_por),
                emissao,
                arquivo_disponivel,
            )
        )

    return SolicitacaoNotaList(
        solicitacoes=solicitacoes,
        resumo_status=[
            SolicitacaoNotaResumoStatus(
                status=status,
                quantidade=quantidade,
                valor_total=valor_total or 0,
            )
            for status, quantidade, valor_total in resumo_rows
        ],
        total=total,
        limit=filtros_query.limit,
        offset=filtros_query.offset,
    )


@router.patch(
    '/solicitacoes-nota/{solicitacao_id}',
    status_code=HTTPStatus.OK,
    response_model=SolicitacaoNotaWorkflowPublic,
)
def atualizar_solicitacao_nota(
    solicitacao_id: int,
    payload: SolicitacaoNotaUpdate,
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
        .where(
            SolicitacaoNota.id == solicitacao_id,
            SolicitacaoNota.ativo.is_(True),
        )
        .with_for_update()
    ).first()
    if row is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Solicitação de nota não encontrada.',
        )

    solicitacao, workflow, cadastrado_por = row
    if workflow.status not in {
        StatusWorkflowSolicitacao.PENDENTE_VALIDACAO.value,
        StatusWorkflowSolicitacao.RECUSADA.value,
    }:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail=(
                'Somente solicitações pendentes de validação ou '
                'recusadas podem ser editadas.'
            ),
        )

    local_anterior = solicitacao.local
    valor_anterior = solicitacao.valor_nota
    solicitacao.local = payload.local.value
    solicitacao.valor_nota = payload.valor_nota
    solicitacao.procedimento = payload.procedimento
    agora = _agora_local()
    workflow.status = StatusWorkflowSolicitacao.PENDENTE_VALIDACAO.value
    workflow.validacao = None
    workflow.motivo_recusa = None
    workflow.validado_por_id = None
    workflow.validado_em = None
    workflow.data_atualizacao = agora
    evento = SolicitacaoNotaEvento(
        solicitacao_nota_id=solicitacao.id,
        usuario_id=usuario_atual.id,
        tipo_acao='EDICAO',
        observacao=(
            f'Local: {local_anterior} -> {payload.local.value}; '
            f'valor: {valor_anterior} -> {payload.valor_nota}; '
            'procedimento atualizado; validação reiniciada.'
        )[:500],
    )
    try:
        session_postgres.add(evento)
        session_postgres.commit()
        session_postgres.refresh(solicitacao)
        session_postgres.refresh(workflow)
    except SQLAlchemyError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Não foi possível atualizar a solicitação de nota.',
        ) from exc

    return _workflow_public(
        solicitacao,
        workflow,
        cadastrado_por,
    )


@router.delete(
    '/solicitacoes-nota/{solicitacao_id}',
    status_code=HTTPStatus.NO_CONTENT,
)
def inativar_solicitacao_nota(
    solicitacao_id: int,
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
):
    row = session_postgres.execute(
        select(
            SolicitacaoNota,
            SolicitacaoNotaWorkflow,
        )
        .join(
            SolicitacaoNotaWorkflow,
            SolicitacaoNotaWorkflow.solicitacao_nota_id
            == SolicitacaoNota.id,
        )
        .where(
            SolicitacaoNota.id == solicitacao_id,
            SolicitacaoNota.ativo.is_(True),
        )
        .with_for_update()
    ).first()
    if row is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Solicitação de nota não encontrada.',
        )

    solicitacao, workflow = row
    if workflow.status not in {
        StatusWorkflowSolicitacao.PENDENTE_VALIDACAO.value,
        StatusWorkflowSolicitacao.RECUSADA.value,
    }:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail=(
                'Somente solicitações pendentes de validação ou '
                'recusadas podem ser inativadas.'
            ),
        )

    solicitacao.ativo = False
    evento = SolicitacaoNotaEvento(
        solicitacao_nota_id=solicitacao.id,
        usuario_id=usuario_atual.id,
        tipo_acao='INATIVACAO',
        observacao=f'Solicitação inativada no status {workflow.status}.',
    )
    try:
        session_postgres.add(evento)
        session_postgres.commit()
    except SQLAlchemyError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Não foi possível inativar a solicitação de nota.',
        ) from exc
    return Response(status_code=HTTPStatus.NO_CONTENT)


@router.get(
    '/solicitacoes-nota/workflow',
    status_code=HTTPStatus.OK,
    response_model=SolicitacaoNotaWorkflowList,
)
def listar_workflow_solicitacoes_nota(
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
    filtros_query: Annotated[SolicitacaoNotaWorkflowFilter, Query()],
    session_oracle: SessionOracle,
):
    del usuario_atual
    filtro_status = (
        SolicitacaoNotaWorkflow.status == filtros_query.status.value
    )
    if filtros_query.incluir_inativas:
        filtros = [
            or_(
                filtro_status,
                SolicitacaoNota.ativo.is_(False),
            )
        ]
    else:
        filtros = [
            SolicitacaoNota.ativo.is_(True),
            filtro_status,
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
    inativador = aliased(Usuario)
    inativacao = aliased(SolicitacaoNotaEvento)
    ultima_inativacao = _ultima_inativacao_subquery()
    base_query = (
        select(
            SolicitacaoNota,
            SolicitacaoNotaWorkflow,
            criador.nome.label('cadastrado_por'),
            validador.nome.label('validado_por'),
            inativacao.usuario_id.label('inativado_por_id'),
            inativador.nome.label('inativado_por'),
            inativacao.data_criacao.label('inativado_em'),
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
        .outerjoin(
            ultima_inativacao,
            ultima_inativacao.c.solicitacao_nota_id
            == SolicitacaoNota.id,
        )
        .outerjoin(
            inativacao,
            inativacao.id == ultima_inativacao.c.evento_id,
        )
        .outerjoin(
            inativador,
            inativador.id == inativacao.usuario_id,
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
    procedimentos_por_atendimento = {}
    procedimentos_disponiveis = True
    if (
        filtros_query.status
        == StatusWorkflowSolicitacao.PENDENTE_VALIDACAO
    ):
        procedimentos_por_atendimento, procedimentos_disponiveis = (
            _consultar_procedimentos_atendimentos(
                {
                    solicitacao.codigo_atendimento
                    for solicitacao, *_restante in rows
                },
                session_oracle,
            )
        )
    return SolicitacaoNotaWorkflowList(
        solicitacoes=[
            _workflow_public(
                solicitacao,
                workflow,
                cadastrado_por,
                validado_por,
                (
                    inativado_por_id,
                    inativado_por,
                    inativado_em,
                ),
            ).model_copy(
                update={
                    'procedimentos_atendimento': (
                        procedimentos_por_atendimento.get(
                            solicitacao.codigo_atendimento,
                            [],
                        )
                    ),
                    'procedimentos_atendimento_disponiveis': (
                        procedimentos_disponiveis
                    ),
                }
            )
            for (
                solicitacao,
                workflow,
                cadastrado_por,
                validado_por,
                inativado_por_id,
                inativado_por,
                inativado_em,
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
        .where(
            SolicitacaoNota.id == solicitacao_id,
            SolicitacaoNota.ativo.is_(True),
        )
        .with_for_update()
    ).first()
    if row is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Solicitação de nota não encontrada.',
        )
    solicitacao, workflow, cadastrado_por = row
    reversao_para_recusa = (
        workflow.status == StatusWorkflowSolicitacao.VALIDADA.value
        and payload.decisao == DecisaoValidacaoSolicitacao.RECUSADA
    )
    if (
        workflow.status
        != StatusWorkflowSolicitacao.PENDENTE_VALIDACAO.value
        and not reversao_para_recusa
    ):
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail=(
                'A solicitação não está disponível para validação '
                'ou reversão.'
            ),
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
        tipo_acao=(
            'REVERSAO_RECUSA'
            if reversao_para_recusa
            else payload.decisao.value
        ),
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


@router.get(
    '/emissoes-nfse',
    status_code=HTTPStatus.OK,
    response_model=SolicitacaoNotaEmissaoList,
)
def listar_emissoes_nfse(
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
    filtros_query: Annotated[SolicitacaoNotaEmissaoFilter, Query()],
):
    del usuario_atual
    status_visiveis = (
        StatusWorkflowSolicitacao.VALIDADA.value,
        StatusWorkflowSolicitacao.EMISSAO_SOLICITADA.value,
        StatusWorkflowSolicitacao.EMITIDA.value,
        StatusWorkflowSolicitacao.ERRO_EMISSAO.value,
    )
    filtros = [
        SolicitacaoNota.ativo.is_(True),
        SolicitacaoNotaWorkflow.validacao
        == DecisaoValidacaoSolicitacao.VALIDADA.value,
        SolicitacaoNotaWorkflow.status.in_(status_visiveis),
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

    ultima_emissao = _ultima_emissao_subquery()
    criador = aliased(Usuario)
    validador = aliased(Usuario)
    base_query = (
        select(
            SolicitacaoNota,
            SolicitacaoNotaWorkflow,
            criador.nome.label('cadastrado_por'),
            validador.nome.label('validado_por'),
            EmissaoNfse,
            EmissaoNfseArquivo.id.label('arquivo_id'),
        )
        .join(
            SolicitacaoNotaWorkflow,
            SolicitacaoNotaWorkflow.solicitacao_nota_id
            == SolicitacaoNota.id,
        )
        .join(criador, criador.id == SolicitacaoNota.usuario_id)
        .outerjoin(
            validador,
            validador.id == SolicitacaoNotaWorkflow.validado_por_id,
        )
        .outerjoin(
            ultima_emissao,
            ultima_emissao.c.solicitacao_nota_id == SolicitacaoNota.id,
        )
        .outerjoin(
            EmissaoNfse,
            EmissaoNfse.id == ultima_emissao.c.emissao_id,
        )
        .outerjoin(
            EmissaoNfseArquivo,
            EmissaoNfseArquivo.emissao_nfse_id == EmissaoNfse.id,
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
    solicitacoes = []
    for (
        solicitacao,
        workflow,
        cadastrado_por,
        validado_por,
        emissao,
        arquivo_id,
    ) in rows:
        arquivo_disponivel = bool(
            emissao
            and emissao.status == StatusEmissaoNfse.EMITIDA.value
            and arquivo_id is not None
        )
        solicitacoes.append(
            _solicitacao_emissao_public(
                solicitacao,
                workflow,
                (cadastrado_por, validado_por),
                emissao,
                arquivo_disponivel,
            )
        )
    return SolicitacaoNotaEmissaoList(
        solicitacoes=solicitacoes,
        total=total,
        limit=filtros_query.limit,
        offset=filtros_query.offset,
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

    workflows = _carregar_workflows_para_emissao(
        payload.solicitacao_ids,
        session_postgres,
    )
    lote, emissoes = _preparar_lote_emissao(
        payload.solicitacao_ids,
        workflows,
        usuario_atual,
        session_postgres,
    )

    try:
        dag_run = disparar_dag_emissao_nfse(
            lote.id,
            payload.solicitacao_ids,
        )
    except AirflowNfseTriggerError as exc:
        _restaurar_apos_falha_airflow(
            lote,
            emissoes,
            usuario_atual,
            exc,
            session_postgres,
        )
        status_code = (
            HTTPStatus.SERVICE_UNAVAILABLE
            if isinstance(exc, AirflowNfseIndisponivelError)
            else HTTPStatus.BAD_GATEWAY
        )
        raise HTTPException(
            status_code=status_code,
            detail=(
                'Não foi possível acionar o Airflow. As solicitações '
                'continuam disponíveis para uma nova tentativa.'
            ),
        ) from exc

    _registrar_disparo_airflow(
        lote,
        emissoes,
        dag_run.dag_run_id,
        usuario_atual,
        session_postgres,
    )

    return _lote_emissao_public(
        lote,
        emissoes,
        message=(
            'Solicitação registrada e DAG do Airflow acionada. '
            f'Execução: {lote.dag_run_id}.'
        ),
    )


@router.get(
    '/emissoes-nfse/itens/{emissao_id}/pdf',
    status_code=HTTPStatus.OK,
    response_class=Response,
)
def consultar_pdf_emissao_nfse(
    emissao_id: int,
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
    download: bool = Query(default=False),
):
    emissao = session_postgres.get(EmissaoNfse, emissao_id)
    if emissao is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Emissão de NFS-e não encontrada.',
        )
    if emissao.status != StatusEmissaoNfse.EMITIDA.value:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='A NFS-e ainda não está disponível.',
        )
    arquivo = session_postgres.scalar(
        select(EmissaoNfseArquivo).where(
            EmissaoNfseArquivo.emissao_nfse_id == emissao.id
        )
    )
    if arquivo is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Arquivo PDF da NFS-e não encontrado.',
        )

    conteudo = bytes(arquivo.conteudo)
    if (
        arquivo.tipo_mime != 'application/pdf'
        or not conteudo.startswith(b'%PDF')
        or arquivo.tamanho_bytes != len(conteudo)
        or arquivo.sha256 != hashlib.sha256(conteudo).hexdigest()
    ):
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail='O arquivo PDF da NFS-e está inválido ou corrompido.',
        )

    nome_arquivo = (
        arquivo.nome_arquivo.replace('\\', '/')
        .rsplit('/', maxsplit=1)[-1]
        .replace('\r', '')
        .replace('\n', '')
        .strip()
    )
    if not nome_arquivo:
        identificador = emissao.numero_nfse or str(emissao.id)
        nome_arquivo = f'NFS-e {identificador}.pdf'
    identificador_fallback = ''.join(
        caractere
        for caractere in str(emissao.numero_nfse or emissao.id)
        if caractere.isalnum() or caractere in '-_'
    )[:80]
    fallback = f'nfse-{identificador_fallback or emissao.id}.pdf'
    disposicao = 'attachment' if download else 'inline'
    tipo_acao = 'DOWNLOAD_NFSE' if download else 'VISUALIZACAO_NFSE'
    evento = SolicitacaoNotaEvento(
        solicitacao_nota_id=emissao.solicitacao_nota_id,
        usuario_id=usuario_atual.id,
        tipo_acao=tipo_acao,
        observacao=(
            f'Emissão #{emissao.id}; NFS-e '
            f'{emissao.numero_nfse or "-"}; arquivo {nome_arquivo}.'
        )[:500],
    )
    try:
        session_postgres.add(evento)
        session_postgres.commit()
    except SQLAlchemyError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Não foi possível registrar o acesso à NFS-e.',
        ) from exc

    return Response(
        content=conteudo,
        media_type='application/pdf',
        headers={
            'Content-Disposition': (
                f'{disposicao}; filename="{fallback}"; '
                f"filename*=UTF-8''{quote(nome_arquivo, safe='')}"
            ),
            'Content-Length': str(len(conteudo)),
            'X-Content-Type-Options': 'nosniff',
        },
    )


@router.get(
    '/emissoes-nfse/{lote_id}',
    status_code=HTTPStatus.OK,
    response_model=LoteEmissaoNfsePublic,
)
def consultar_emissao_nfse(
    lote_id: int,
    _usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
):
    lote = session_postgres.get(LoteEmissaoNfse, lote_id)
    if lote is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Lote de emissão de NFS-e não encontrado.',
        )
    emissoes = list(
        session_postgres.scalars(
            select(EmissaoNfse)
            .where(EmissaoNfse.lote_id == lote_id)
            .order_by(EmissaoNfse.id)
        ).all()
    )
    return _lote_emissao_public(lote, emissoes)


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
