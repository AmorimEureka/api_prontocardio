import hashlib
import re
from datetime import datetime, time, timedelta
from decimal import Decimal
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
    EmpresaEmissora,
    EmpresaEmissoraEvento,
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
    AcompanhamentoParticularFilter,
    AcompanhamentoParticularItem,
    AcompanhamentoParticularList,
    AcompanhamentoParticularPacienteDia,
    AcompanhamentoParticularResumoDia,
    AcompanhamentoParticularResumoStatus,
    AtendimentoSolicitacaoNotaPublic,
    EmissaoNfseCreate,
    EmissaoNfsePublic,
    EmpresaEmissoraCreate,
    EmpresaEmissoraPublic,
    EmpresaEmissoraStatusUpdate,
    EmpresaEmissoraUpdate,
    EmpresasEmissorasList,
    LoteEmissaoNfsePublic,
    ProcedimentoAtendimentoPublic,
    SolicitacaoAtendimentoHistoricoPublic,
    SolicitacaoNotaCreate,
    SolicitacaoNotaEmissaoFilter,
    SolicitacaoNotaEmissaoList,
    SolicitacaoNotaEmissaoPublic,
    SolicitacaoNotaEmpresaEmissoraInput,
    SolicitacaoNotaFilter,
    SolicitacaoNotaList,
    SolicitacaoNotaPublic,
    SolicitacaoNotaResumoStatus,
    SolicitacaoNotaUpdate,
    SolicitacaoNotaWorkflowFilter,
    SolicitacaoNotaWorkflowList,
    SolicitacaoNotaWorkflowPublic,
    SolicitacoesAtendimentoHistoricoList,
    StatusAcompanhamentoParticular,
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
CONVENIO_PARTICULAR = 'PARTICULAR'


def _texto(value) -> str | None:
    texto = str(value or '').strip()
    return texto or None


def _telefone_com_ddd(ddd, telefone) -> str | None:
    numero = re.sub(r'\D', '', _texto(telefone) or '')
    if not numero:
        return None
    if len(numero) not in {8, 9}:
        return numero

    codigo_area = re.sub(r'\D', '', _texto(ddd) or '')
    if len(codigo_area) == 3 and codigo_area.startswith('0'):
        codigo_area = codigo_area[1:]
    if len(codigo_area) == 2:
        return f'{codigo_area}{numero}'
    return numero


def _agora_local() -> datetime:
    return datetime.now(ZoneInfo('America/Sao_Paulo')).replace(tzinfo=None)


def _dados_empresa(empresa: EmpresaEmissora) -> dict:
    return {
        'cnpj': empresa.cnpj,
        'razao_social': empresa.razao_social,
        'ativo': empresa.ativo,
    }


def _empresa_public(
    empresa: EmpresaEmissora,
    criado_por: str | None = None,
    atualizado_por: str | None = None,
) -> EmpresaEmissoraPublic:
    return EmpresaEmissoraPublic.model_validate(empresa).model_copy(
        update={
            'criado_por': criado_por,
            'atualizado_por': atualizado_por,
        }
    )


def _registrar_evento_empresa(
    session: Session,
    empresa: EmpresaEmissora,
    usuario_id: int | None,
    tipo_acao: str,
    dados_anteriores: dict | None = None,
) -> None:
    session.add(
        EmpresaEmissoraEvento(
            empresa_emissora_id=empresa.id,
            usuario_id=usuario_id,
            tipo_acao=tipo_acao,
            dados_anteriores=dados_anteriores,
            dados_novos=_dados_empresa(empresa),
        )
    )


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
            EmissaoNfsePublic.model_validate(emissao) for emissao in emissoes
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
    dados_workflow = _workflow_public(
        solicitacao,
        workflow,
        cadastrado_por,
        validado_por,
    ).model_dump()
    if emissao is not None:
        dados_workflow.update({
            'empresa_emissora_id': emissao.empresa_emissora_id,
            'cnpj_emissor': emissao.cnpj_emissor,
            'razao_social_emissor': emissao.razao_social_emissor,
        })
    return SolicitacaoNotaEmissaoPublic(
        **dados_workflow,
        emissao_id=emissao.id if emissao else None,
        lote_id=emissao.lote_id if emissao else None,
        status_emissao=emissao.status if emissao else None,
        numero_nfse=emissao.numero_nfse if emissao else None,
        protocolo=emissao.protocolo if emissao else None,
        erro_emissao=emissao.erro if emissao else None,
        emissao_criada_em=emissao.data_criacao if emissao else None,
        emissao_atualizada_em=(emissao.data_atualizacao if emissao else None),
        arquivo_disponivel=arquivo_disponivel,
    )


def _ultima_emissao_subquery():
    return (
        select(
            EmissaoNfse.solicitacao_nota_id.label('solicitacao_nota_id'),
            func.max(EmissaoNfse.id).label('emissao_id'),
        )
        .group_by(EmissaoNfse.solicitacao_nota_id)
        .subquery()
    )


def _consultar_atendimentos_particulares(
    session_oracle: Session,
    filtros: AcompanhamentoParticularFilter,
) -> list[dict]:
    inicio = datetime.combine(filtros.data_inicio, time.min)
    fim = datetime.combine(
        filtros.data_fim + timedelta(days=1),
        time.min,
    )
    filtros_oracle = [
        ModelContaAtendimento.cd_atendimento.is_not(None),
        ModelContaAtendimento.cd_paciente.is_not(None),
        ModelContaAtendimento.cd_convenio.is_not(None),
        ModelContaAtendimento.nm_paciente.is_not(None),
        ModelContaAtendimento.dt_atendimento >= inicio,
        ModelContaAtendimento.dt_atendimento < fim,
        func.upper(func.trim(ModelContaAtendimento.nm_convenio))
        == CONVENIO_PARTICULAR,
    ]
    if filtros.codigo_atendimento is not None:
        filtros_oracle.append(
            ModelContaAtendimento.cd_atendimento
            == filtros.codigo_atendimento
        )
    if nome_paciente := _texto(filtros.nome_paciente):
        filtros_oracle.append(
            ModelContaAtendimento.nm_paciente.ilike(
                f'%{nome_paciente}%'
            )
        )
    if filtros.tipo_atendimento is not None:
        filtros_oracle.append(
            ModelContaAtendimento.tp_atendimento
            == filtros.tipo_atendimento.value
        )

    contas = (
        select(
            ModelContaAtendimento.cd_atendimento.label(
                'codigo_atendimento'
            ),
            ModelContaAtendimento.cd_paciente.label('codigo_paciente'),
            ModelContaAtendimento.cd_convenio.label('codigo_convenio'),
            ModelContaAtendimento.nm_paciente.label('nome_paciente'),
            ModelContaAtendimento.nm_convenio.label('convenio'),
            ModelContaAtendimento.tp_atendimento.label(
                'tipo_atendimento'
            ),
            ModelContaAtendimento.cd_reg.label('codigo_conta'),
            func.min(ModelContaAtendimento.dt_atendimento).label(
                'data_atendimento'
            ),
            func.max(ModelContaAtendimento.dt_alta).label('data_alta'),
            func.coalesce(
                func.max(ModelContaAtendimento.vl_total_registro),
                0,
            ).label('valor_conta'),
            func.count().label('quantidade_lancamentos'),
        )
        .where(*filtros_oracle)
        .group_by(
            ModelContaAtendimento.cd_atendimento,
            ModelContaAtendimento.cd_paciente,
            ModelContaAtendimento.cd_convenio,
            ModelContaAtendimento.nm_paciente,
            ModelContaAtendimento.nm_convenio,
            ModelContaAtendimento.tp_atendimento,
            ModelContaAtendimento.cd_reg,
        )
        .subquery()
    )
    query = (
        select(
            contas.c.codigo_atendimento,
            contas.c.codigo_paciente,
            contas.c.codigo_convenio,
            contas.c.nome_paciente,
            contas.c.convenio,
            contas.c.tipo_atendimento,
            func.min(contas.c.data_atendimento).label('data_atendimento'),
            func.max(contas.c.data_alta).label('data_alta'),
            func.coalesce(func.sum(contas.c.valor_conta), 0).label(
                'valor_conta'
            ),
            func.sum(contas.c.quantidade_lancamentos).label(
                'quantidade_lancamentos'
            ),
        )
        .group_by(
            contas.c.codigo_atendimento,
            contas.c.codigo_paciente,
            contas.c.codigo_convenio,
            contas.c.nome_paciente,
            contas.c.convenio,
            contas.c.tipo_atendimento,
        )
        .order_by(
            func.min(contas.c.data_atendimento),
            contas.c.nome_paciente,
            contas.c.codigo_atendimento,
        )
    )
    return [
        dict(row._mapping)
        for row in session_oracle.execute(query).all()
    ]


def _solicitacoes_mais_recentes_por_atendimento(
    codigos_atendimento: set[int],
    session_postgres: Session,
) -> dict[int, tuple]:
    if not codigos_atendimento:
        return {}

    ultima_emissao = _ultima_emissao_subquery()
    rows = session_postgres.execute(
        select(
            SolicitacaoNota,
            SolicitacaoNotaWorkflow,
            EmissaoNfse,
            EmissaoNfseArquivo.id.label('arquivo_id'),
        )
        .join(
            SolicitacaoNotaWorkflow,
            SolicitacaoNotaWorkflow.solicitacao_nota_id
            == SolicitacaoNota.id,
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
        .where(
            SolicitacaoNota.codigo_atendimento.in_(codigos_atendimento)
        )
        .order_by(
            SolicitacaoNota.codigo_atendimento,
            SolicitacaoNota.ativo.desc(),
            SolicitacaoNota.id.desc(),
        )
    ).all()
    recentes = {}
    for solicitacao, workflow, emissao, arquivo_id in rows:
        recentes.setdefault(
            solicitacao.codigo_atendimento,
            (solicitacao, workflow, emissao, arquivo_id),
        )
    return recentes


def _status_acompanhamento_particular(
    solicitacao: SolicitacaoNota | None,
    workflow: SolicitacaoNotaWorkflow | None,
    emissao: EmissaoNfse | None,
) -> StatusAcompanhamentoParticular:
    if solicitacao is None or workflow is None:
        status = StatusAcompanhamentoParticular.SEM_SOLICITACAO
    elif not solicitacao.ativo:
        status = StatusAcompanhamentoParticular.INATIVA
    else:
        status_emissao = emissao.status if emissao is not None else None
        if (
            status_emissao == StatusEmissaoNfse.EMITIDA.value
            or workflow.status == StatusWorkflowSolicitacao.EMITIDA.value
        ):
            status = StatusAcompanhamentoParticular.EMITIDA
        elif (
            status_emissao == StatusEmissaoNfse.ERRO.value
            or workflow.status
            == StatusWorkflowSolicitacao.ERRO_EMISSAO.value
        ):
            status = StatusAcompanhamentoParticular.ERRO_EMISSAO
        elif status_emissao == StatusEmissaoNfse.PROCESSANDO.value:
            status = StatusAcompanhamentoParticular.PROCESSANDO
        elif (
            status_emissao == StatusEmissaoNfse.PENDENTE.value
            or workflow.status
            == StatusWorkflowSolicitacao.EMISSAO_SOLICITADA.value
        ):
            status = StatusAcompanhamentoParticular.PENDENTE_EMISSAO
        else:
            status = StatusAcompanhamentoParticular(workflow.status)

    return status


@router.get(
    '/acompanhamento-particular',
    status_code=HTTPStatus.OK,
    response_model=AcompanhamentoParticularList,
)
def acompanhar_atendimentos_particulares(
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
    session_oracle: SessionOracle,
    filtros_query: Annotated[AcompanhamentoParticularFilter, Query()],
):
    del usuario_atual
    try:
        atendimentos_oracle = _consultar_atendimentos_particulares(
            session_oracle,
            filtros_query,
        )
        solicitacoes_por_atendimento = (
            _solicitacoes_mais_recentes_por_atendimento(
                {
                    atendimento['codigo_atendimento']
                    for atendimento in atendimentos_oracle
                },
                session_postgres,
            )
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail=(
                'Não foi possível carregar os atendimentos particulares '
                'no momento.'
            ),
        ) from exc

    atendimentos = []
    for atendimento in atendimentos_oracle:
        dados_solicitacao = solicitacoes_por_atendimento.get(
            atendimento['codigo_atendimento']
        )
        if dados_solicitacao is None:
            solicitacao = workflow = emissao = arquivo_id = None
        else:
            solicitacao, workflow, emissao, arquivo_id = dados_solicitacao

        status = _status_acompanhamento_particular(
            solicitacao,
            workflow,
            emissao,
        )
        atendimentos.append(
            AcompanhamentoParticularItem(
                **atendimento,
                status=status,
                solicitacao_id=(
                    solicitacao.id if solicitacao is not None else None
                ),
                workflow_status=(
                    workflow.status if workflow is not None else None
                ),
                emissao_id=emissao.id if emissao is not None else None,
                emissao_status=(
                    emissao.status if emissao is not None else None
                ),
                numero_nfse=(
                    emissao.numero_nfse if emissao is not None else None
                ),
                erro_emissao=(
                    emissao.erro if emissao is not None else None
                ),
                arquivo_disponivel=bool(
                    emissao is not None
                    and emissao.status == StatusEmissaoNfse.EMITIDA.value
                    and arquivo_id is not None
                ),
                solicitada_em=(
                    solicitacao.data_criacao
                    if solicitacao is not None
                    else None
                ),
                atualizada_em=(
                    emissao.data_atualizacao
                    if emissao is not None
                    else (
                        workflow.data_atualizacao
                        if workflow is not None
                        else None
                    )
                ),
            )
        )

    resumo = {
        status: {
            'quantidade': 0,
            'valor_total': Decimal('0'),
        }
        for status in StatusAcompanhamentoParticular
    }
    for atendimento in atendimentos:
        resumo[atendimento.status]['quantidade'] += 1
        resumo[atendimento.status]['valor_total'] += (
            atendimento.valor_conta or Decimal('0')
        )

    resumo_diario = {}
    for atendimento in atendimentos:
        data_atendimento = atendimento.data_atendimento.date()
        resumo_dia = resumo_diario.setdefault(
            data_atendimento,
            {
                'total': 0,
                'emitidas': 0,
                'valor_total': Decimal('0'),
                'pacientes': [],
                'status': {
                    status: {
                        'quantidade': 0,
                        'valor_total': Decimal('0'),
                    }
                    for status in StatusAcompanhamentoParticular
                },
            },
        )
        valor_conta = atendimento.valor_conta or Decimal('0')
        resumo_dia['total'] += 1
        resumo_dia['valor_total'] += valor_conta
        resumo_dia['status'][atendimento.status]['quantidade'] += 1
        resumo_dia['status'][atendimento.status]['valor_total'] += valor_conta
        resumo_dia['pacientes'].append(
            AcompanhamentoParticularPacienteDia(
                nome=atendimento.nome_paciente,
                inicial=(atendimento.nome_paciente.strip()[:1].upper() or '?'),
                status=atendimento.status,
            )
        )
        if atendimento.status == StatusAcompanhamentoParticular.EMITIDA:
            resumo_dia['emitidas'] += 1

    total_periodo = len(atendimentos)
    valor_total_periodo = sum(
        (
            atendimento.valor_conta or Decimal('0')
            for atendimento in atendimentos
        ),
        Decimal('0'),
    )
    if filtros_query.status is not None:
        atendimentos = [
            atendimento
            for atendimento in atendimentos
            if atendimento.status == filtros_query.status
        ]

    total = len(atendimentos)
    inicio = filtros_query.offset
    fim = inicio + filtros_query.limit
    pagina_atendimentos = atendimentos[inicio:fim]
    codigos_pagina = {
        atendimento.codigo_atendimento
        for atendimento in pagina_atendimentos
        if atendimento.solicitacao_id is not None
    }
    procedimentos_por_atendimento, procedimentos_disponiveis = (
        _consultar_procedimentos_atendimentos(
            codigos_pagina,
            session_oracle,
        )
    )
    historicos_por_atendimento = _consultar_solicitacoes_atendimentos(
        codigos_pagina,
        session_postgres,
    )
    ids_usuarios = set()
    for codigo_atendimento in codigos_pagina:
        solicitacao, workflow, _emissao, _arquivo_id = (
            solicitacoes_por_atendimento[codigo_atendimento]
        )
        ids_usuarios.add(solicitacao.usuario_id)
        if workflow.validado_por_id is not None:
            ids_usuarios.add(workflow.validado_por_id)
    nomes_usuarios = {}
    if ids_usuarios:
        nomes_usuarios = dict(
            session_postgres.execute(
                select(Usuario.id, Usuario.nome).where(
                    Usuario.id.in_(ids_usuarios)
                )
            ).all()
        )
    pagina_enriquecida = []
    for atendimento in pagina_atendimentos:
        dados_solicitacao = solicitacoes_por_atendimento.get(
            atendimento.codigo_atendimento
        )
        if dados_solicitacao is None:
            pagina_enriquecida.append(atendimento)
            continue
        solicitacao, workflow, _emissao, _arquivo_id = dados_solicitacao
        solicitacao_publica = _workflow_public(
            solicitacao,
            workflow,
            cadastrado_por=nomes_usuarios.get(solicitacao.usuario_id),
            validado_por=nomes_usuarios.get(workflow.validado_por_id),
        ).model_copy(
            update={
                'procedimentos_atendimento': (
                    procedimentos_por_atendimento.get(
                        atendimento.codigo_atendimento,
                        [],
                    )
                ),
                'procedimentos_atendimento_disponiveis': (
                    procedimentos_disponiveis
                ),
                'valor_total_procedimentos': (
                    _somar_valores_procedimentos(
                        procedimentos_por_atendimento.get(
                            atendimento.codigo_atendimento,
                            [],
                        )
                    )
                ),
                'solicitacoes_anteriores': [
                    anterior
                    for anterior in historicos_por_atendimento.get(
                        atendimento.codigo_atendimento,
                        [],
                    )
                    if (
                        anterior.data_criacao < solicitacao.data_criacao
                        or (
                            anterior.data_criacao == solicitacao.data_criacao
                            and anterior.id < solicitacao.id
                        )
                    )
                ],
            }
        )
        pagina_enriquecida.append(
            atendimento.model_copy(update={'solicitacao': solicitacao_publica})
        )
    return AcompanhamentoParticularList(
        atendimentos=pagina_enriquecida,
        resumo_status=[
            AcompanhamentoParticularResumoStatus(
                status=status,
                **resumo[status],
            )
            for status in StatusAcompanhamentoParticular
        ],
        resumo_diario=[
            AcompanhamentoParticularResumoDia(
                data=data_resumo,
                total=dados['total'],
                emitidas=dados['emitidas'],
                pendentes=dados['total'] - dados['emitidas'],
                valor_total=dados['valor_total'],
                resumo_status=[
                    AcompanhamentoParticularResumoStatus(
                        status=status,
                        **dados['status'][status],
                    )
                    for status in StatusAcompanhamentoParticular
                ],
                pacientes=dados['pacientes'][:3],
                pacientes_restantes=max(
                    dados['total'] - len(dados['pacientes'][:3]),
                    0,
                ),
            )
            for data_resumo, dados in sorted(resumo_diario.items())
        ],
        data_inicio=filtros_query.data_inicio,
        data_fim=filtros_query.data_fim,
        total_periodo=total_periodo,
        total=total,
        valor_total_periodo=valor_total_periodo,
        limit=filtros_query.limit,
        offset=filtros_query.offset,
    )


def _somar_valores_procedimentos(
    procedimentos: list[ProcedimentoAtendimentoPublic],
) -> Decimal:
    return sum(
        (
            procedimento.valor_total or Decimal('0')
            for procedimento in procedimentos
        ),
        Decimal('0'),
    )


def _consultar_solicitacoes_atendimentos(
    codigos_atendimento: set[int],
    session_postgres: Session,
) -> dict[int, list[SolicitacaoAtendimentoHistoricoPublic]]:
    solicitacoes_por_atendimento = {
        codigo: [] for codigo in codigos_atendimento
    }
    if not codigos_atendimento:
        return solicitacoes_por_atendimento

    ultima_emissao = _ultima_emissao_subquery()
    ultima_inativacao = _ultima_inativacao_subquery()
    inativacao = aliased(SolicitacaoNotaEvento)
    rows = session_postgres.execute(
        select(
            SolicitacaoNota,
            SolicitacaoNotaWorkflow,
            EmissaoNfse,
            EmissaoNfseArquivo.id.label('arquivo_id'),
            Usuario.nome.label('cadastrado_por'),
            inativacao.observacao.label('motivo_inativacao'),
        )
        .join(
            SolicitacaoNotaWorkflow,
            SolicitacaoNotaWorkflow.solicitacao_nota_id == SolicitacaoNota.id,
        )
        .join(Usuario, Usuario.id == SolicitacaoNota.usuario_id)
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
        .outerjoin(
            ultima_inativacao,
            ultima_inativacao.c.solicitacao_nota_id == SolicitacaoNota.id,
        )
        .outerjoin(
            inativacao,
            inativacao.id == ultima_inativacao.c.evento_id,
        )
        .where(SolicitacaoNota.codigo_atendimento.in_(codigos_atendimento))
        .order_by(
            SolicitacaoNota.codigo_atendimento,
            SolicitacaoNota.data_criacao.desc(),
            SolicitacaoNota.id.desc(),
        )
    ).all()
    for (
        solicitacao,
        workflow,
        emissao,
        arquivo_id,
        cadastrado_por,
        motivo_inativacao,
    ) in rows:
        solicitacoes_por_atendimento.setdefault(
            solicitacao.codigo_atendimento,
            [],
        ).append(
            SolicitacaoAtendimentoHistoricoPublic(
                id=solicitacao.id,
                local=solicitacao.local,
                procedimento=solicitacao.procedimento,
                valor_nota=solicitacao.valor_nota,
                cadastrado_por=cadastrado_por,
                motivo=workflow.motivo_recusa or motivo_inativacao,
                status=workflow.status,
                ativo=solicitacao.ativo,
                data_criacao=solicitacao.data_criacao,
                validado_em=workflow.validado_em,
                emissao_id=emissao.id if emissao else None,
                status_emissao=emissao.status if emissao else None,
                numero_nfse=emissao.numero_nfse if emissao else None,
                arquivo_disponivel=bool(
                    emissao
                    and emissao.status == StatusEmissaoNfse.EMITIDA.value
                    and arquivo_id
                ),
            )
        )
    return solicitacoes_por_atendimento


def _consultar_solicitacoes_atendimento(
    codigo_atendimento: int,
    session_postgres: Session,
) -> SolicitacoesAtendimentoHistoricoList:
    solicitacoes = _consultar_solicitacoes_atendimentos(
        {codigo_atendimento},
        session_postgres,
    ).get(codigo_atendimento, [])
    return SolicitacoesAtendimentoHistoricoList(
        solicitacoes=solicitacoes,
        total=len(solicitacoes),
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
                ModelContaAtendimento.vl_total_conta,
                ModelContaAtendimento.dt_lancamento,
                ModelContaAtendimento.nm_prestador,
            )
            .where(
                ModelContaAtendimento.cd_atendimento.in_(codigos_atendimento)
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
                valor_total=row.vl_total_conta,
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
            solicitacao.cnpj_emissor,
            solicitacao.razao_social_emissor,
        )
    )


def _carregar_workflows_para_emissao(
    solicitacao_ids: list[int],
    session_postgres: Session,
) -> dict[int, tuple[SolicitacaoNota, SolicitacaoNotaWorkflow]]:
    rows = session_postgres.execute(
        select(
            SolicitacaoNota,
            SolicitacaoNotaWorkflow,
        )
        .join(
            SolicitacaoNotaWorkflow,
            SolicitacaoNota.id == SolicitacaoNotaWorkflow.solicitacao_nota_id,
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
    workflows = {solicitacao.id: workflow for solicitacao, workflow in rows}
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
    status_disponiveis = {
        StatusWorkflowSolicitacao.VALIDADA.value,
        StatusWorkflowSolicitacao.ERRO_EMISSAO.value,
    }
    indisponiveis = [
        solicitacao_id
        for solicitacao_id in solicitacao_ids
        if (
            solicitacao_id not in solicitacoes
            or solicitacao_id in emissoes_existentes
            or workflows[solicitacao_id].status not in status_disponiveis
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
                'Somente solicitações validadas ou com erro de emissão '
                'podem ser emitidas. '
                f'Verifique os registros: {indisponiveis}.'
            ),
        )
    return {
        solicitacao_id: (solicitacoes[solicitacao_id], workflow)
        for solicitacao_id, workflow in workflows.items()
    }


def _preparar_lote_emissao(
    solicitacao_ids: list[int],
    solicitacoes_workflows: dict[
        int,
        tuple[SolicitacaoNota, SolicitacaoNotaWorkflow],
    ],
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
            solicitacao, workflow = solicitacoes_workflows[solicitacao_id]
            emissao = EmissaoNfse(
                solicitacao_nota_id=solicitacao_id,
                lote_id=lote.id,
                usuario_id=usuario_atual.id,
                status=StatusEmissaoNfse.PENDENTE.value,
                empresa_emissora_id=solicitacao.empresa_emissora_id,
                cnpj_emissor=solicitacao.cnpj_emissor,
                razao_social_emissor=solicitacao.razao_social_emissor,
            )
            emissoes.append(emissao)
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
                SolicitacaoNotaWorkflow.solicitacao_nota_id.in_([
                    emissao.solicitacao_nota_id for emissao in emissoes
                ])
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
                observacao=(f'Lote #{lote.id}; dag_run_id={dag_run_id}.')[
                    :500
                ],
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
            .where(ModelContaAtendimento.cd_atendimento == codigo_atendimento)
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
    procedimentos_atendimento = procedimentos.get(codigo_atendimento, [])
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
        nr_fone=_telefone_com_ddd(paciente.ddd, paciente.contato),
        tipo_atendimento=(
            _texto(atendimento.tp_atendimento) or 'Não informado'
        ),
        procedimentos_atendimento=procedimentos_atendimento,
        procedimentos_atendimento_disponiveis=procedimentos_disponiveis,
        valor_total_procedimentos=_somar_valores_procedimentos(
            procedimentos_atendimento
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
    '/atendimentos/{codigo_atendimento}/solicitacoes',
    status_code=HTTPStatus.OK,
    response_model=SolicitacoesAtendimentoHistoricoList,
)
def consultar_solicitacoes_atendimento(
    codigo_atendimento: int,
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
):
    del usuario_atual
    return _consultar_solicitacoes_atendimento(
        codigo_atendimento,
        session_postgres,
    )


@router.get(
    '/empresas-emissoras',
    status_code=HTTPStatus.OK,
    response_model=EmpresasEmissorasList,
)
def listar_empresas_emissoras(
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
    incluir_inativas: bool = False,
):
    del usuario_atual
    criador = aliased(Usuario)
    atualizador = aliased(Usuario)
    query = (
        select(
            EmpresaEmissora,
            criador.nome.label('criado_por'),
            atualizador.nome.label('atualizado_por'),
        )
        .outerjoin(
            criador,
            criador.id == EmpresaEmissora.usuario_criacao_id,
        )
        .outerjoin(
            atualizador,
            atualizador.id == EmpresaEmissora.usuario_atualizacao_id,
        )
    )
    if not incluir_inativas:
        query = query.where(EmpresaEmissora.ativo.is_(True))
    rows = session_postgres.execute(
        query.order_by(
            EmpresaEmissora.ativo.desc(),
            EmpresaEmissora.razao_social,
        )
    ).all()
    return EmpresasEmissorasList(
        empresas=[
            _empresa_public(empresa, criado_por, atualizado_por)
            for empresa, criado_por, atualizado_por in rows
        ],
        total=len(rows),
    )


@router.post(
    '/empresas-emissoras',
    status_code=HTTPStatus.CREATED,
    response_model=EmpresaEmissoraPublic,
)
def cadastrar_empresa_emissora(
    payload: EmpresaEmissoraCreate,
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
):
    empresa = EmpresaEmissora(
        cnpj=payload.cnpj,
        razao_social=payload.razao_social,
        usuario_criacao_id=usuario_atual.id,
        usuario_atualizacao_id=usuario_atual.id,
    )
    agora = _agora_local()
    empresa.data_criacao = agora
    empresa.data_atualizacao = agora
    try:
        session_postgres.add(empresa)
        session_postgres.flush()
        _registrar_evento_empresa(
            session_postgres,
            empresa,
            usuario_atual.id,
            'CRIACAO',
        )
        session_postgres.commit()
        session_postgres.refresh(empresa)
    except IntegrityError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='Já existe uma empresa cadastrada com este CNPJ.',
        ) from exc
    except SQLAlchemyError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Não foi possível cadastrar a empresa emissora.',
        ) from exc
    return _empresa_public(
        empresa,
        usuario_atual.nome,
        usuario_atual.nome,
    )


@router.put(
    '/empresas-emissoras/{empresa_id}',
    status_code=HTTPStatus.OK,
    response_model=EmpresaEmissoraPublic,
)
def atualizar_empresa_emissora(
    empresa_id: int,
    payload: EmpresaEmissoraUpdate,
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
):
    empresa = session_postgres.scalar(
        select(EmpresaEmissora)
        .where(EmpresaEmissora.id == empresa_id)
        .with_for_update()
    )
    if empresa is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Empresa emissora não encontrada.',
        )
    dados_anteriores = _dados_empresa(empresa)
    empresa.cnpj = payload.cnpj
    empresa.razao_social = payload.razao_social
    empresa.usuario_atualizacao_id = usuario_atual.id
    empresa.data_atualizacao = _agora_local()
    try:
        _registrar_evento_empresa(
            session_postgres,
            empresa,
            usuario_atual.id,
            'ATUALIZACAO',
            dados_anteriores,
        )
        session_postgres.commit()
        session_postgres.refresh(empresa)
    except IntegrityError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='Já existe uma empresa cadastrada com este CNPJ.',
        ) from exc
    except SQLAlchemyError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Não foi possível atualizar a empresa emissora.',
        ) from exc
    criado_por = session_postgres.scalar(
        select(Usuario.nome).where(Usuario.id == empresa.usuario_criacao_id)
    )
    return _empresa_public(
        empresa,
        criado_por,
        usuario_atual.nome,
    )


@router.patch(
    '/empresas-emissoras/{empresa_id}/status',
    status_code=HTTPStatus.OK,
    response_model=EmpresaEmissoraPublic,
)
def atualizar_status_empresa_emissora(
    empresa_id: int,
    payload: EmpresaEmissoraStatusUpdate,
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
):
    empresa = session_postgres.scalar(
        select(EmpresaEmissora)
        .where(EmpresaEmissora.id == empresa_id)
        .with_for_update()
    )
    if empresa is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Empresa emissora não encontrada.',
        )
    dados_anteriores = _dados_empresa(empresa)
    empresa.ativo = payload.ativo
    empresa.usuario_atualizacao_id = usuario_atual.id
    empresa.data_atualizacao = _agora_local()
    try:
        _registrar_evento_empresa(
            session_postgres,
            empresa,
            usuario_atual.id,
            'REATIVACAO' if payload.ativo else 'INATIVACAO',
            dados_anteriores,
        )
        session_postgres.commit()
        session_postgres.refresh(empresa)
    except SQLAlchemyError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Não foi possível alterar o estado da empresa emissora.',
        ) from exc
    criado_por = session_postgres.scalar(
        select(Usuario.nome).where(Usuario.id == empresa.usuario_criacao_id)
    )
    return _empresa_public(
        empresa,
        criado_por,
        usuario_atual.nome,
    )


@router.put(
    '/solicitacoes-nota/{solicitacao_id}/empresa-emissora',
    status_code=HTTPStatus.OK,
    response_model=SolicitacaoNotaWorkflowPublic,
)
def selecionar_empresa_emissora_solicitacao(
    solicitacao_id: int,
    payload: SolicitacaoNotaEmpresaEmissoraInput,
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
            SolicitacaoNotaWorkflow.solicitacao_nota_id == SolicitacaoNota.id,
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
    if workflow.status != StatusWorkflowSolicitacao.PENDENTE_VALIDACAO.value:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail=(
                'A empresa emissora só pode ser selecionada antes '
                'da validação.'
            ),
        )
    empresa = session_postgres.scalar(
        select(EmpresaEmissora).where(
            EmpresaEmissora.id == payload.empresa_emissora_id,
            EmpresaEmissora.ativo.is_(True),
        )
    )
    if empresa is None:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='Selecione uma empresa emissora ativa.',
        )

    cnpj_anterior = solicitacao.cnpj_emissor
    solicitacao.empresa_emissora_id = empresa.id
    solicitacao.cnpj_emissor = empresa.cnpj
    solicitacao.razao_social_emissor = empresa.razao_social
    workflow.data_atualizacao = _agora_local()
    session_postgres.add(
        SolicitacaoNotaEvento(
            solicitacao_nota_id=solicitacao.id,
            usuario_id=usuario_atual.id,
            tipo_acao='EMPRESA_EMISSORA_SELECIONADA',
            observacao=(
                f'CNPJ emissor alterado de {cnpj_anterior or "não informado"} '
                f'para {empresa.cnpj} ({empresa.razao_social}).'
            )[:500],
        )
    )
    try:
        session_postgres.commit()
        session_postgres.refresh(workflow)
    except SQLAlchemyError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Não foi possível selecionar a empresa emissora.',
        ) from exc
    return _workflow_public(
        solicitacao,
        workflow,
        cadastrado_por=cadastrado_por,
    )


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
        filtros_base.append(SolicitacaoNota.convenio.ilike(f'%{convenio}%'))
    if filtros_query.local:
        filtros_base.append(SolicitacaoNota.local == filtros_query.local.value)

    filtros = list(filtros_base)
    if filtros_query.status:
        filtros.append(
            SolicitacaoNotaWorkflow.status == filtros_query.status.value
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
            SolicitacaoNotaWorkflow.solicitacao_nota_id == SolicitacaoNota.id,
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
    total = (
        session_postgres.scalar(
            select(func.count()).select_from(base_query.subquery())
        )
        or 0
    )

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
            SolicitacaoNotaWorkflow.solicitacao_nota_id == SolicitacaoNota.id,
        )
        .where(*filtros_base)
        .group_by(SolicitacaoNotaWorkflow.status)
    ).all()

    rows = session_postgres.execute(
        base_query
        .order_by(
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
            SolicitacaoNotaWorkflow.solicitacao_nota_id == SolicitacaoNota.id,
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
            SolicitacaoNotaWorkflow.solicitacao_nota_id == SolicitacaoNota.id,
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
        filtros.append(SolicitacaoNota.nm_paciente.ilike(f'%{nome_paciente}%'))
    if cpf := _texto(filtros_query.cpf):
        filtros.append(SolicitacaoNota.nr_cpf.ilike(f'%{cpf}%'))
    if tipo_atendimento := _texto(filtros_query.tipo_atendimento):
        filtros.append(SolicitacaoNota.tipo_atendimento == tipo_atendimento)
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
            SolicitacaoNotaWorkflow.solicitacao_nota_id == SolicitacaoNota.id,
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
            ultima_inativacao.c.solicitacao_nota_id == SolicitacaoNota.id,
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
    total = (
        session_postgres.scalar(
            select(func.count()).select_from(base_query.subquery())
        )
        or 0
    )
    rows = session_postgres.execute(
        base_query
        .order_by(
            SolicitacaoNotaWorkflow.data_atualizacao.desc(),
            SolicitacaoNota.id.desc(),
        )
        .limit(filtros_query.limit)
        .offset(filtros_query.offset)
    ).all()
    procedimentos_por_atendimento = {}
    procedimentos_disponiveis = True
    if filtros_query.status == StatusWorkflowSolicitacao.PENDENTE_VALIDACAO:
        procedimentos_por_atendimento, procedimentos_disponiveis = (
            _consultar_procedimentos_atendimentos(
                {
                    solicitacao.codigo_atendimento
                    for solicitacao, *_restante in rows
                },
                session_oracle,
            )
        )
    solicitacoes_por_atendimento = _consultar_solicitacoes_atendimentos(
        {solicitacao.codigo_atendimento for solicitacao, *_restante in rows},
        session_postgres,
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
                    'valor_total_procedimentos': (
                        _somar_valores_procedimentos(
                            procedimentos_por_atendimento.get(
                                solicitacao.codigo_atendimento,
                                [],
                            )
                        )
                    ),
                    'solicitacoes_anteriores': [
                        anterior
                        for anterior in solicitacoes_por_atendimento.get(
                            solicitacao.codigo_atendimento,
                            [],
                        )
                        if (
                            anterior.data_criacao < solicitacao.data_criacao
                            or (
                                anterior.data_criacao
                                == solicitacao.data_criacao
                                and anterior.id < solicitacao.id
                            )
                        )
                    ],
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
            SolicitacaoNotaWorkflow.solicitacao_nota_id == SolicitacaoNota.id,
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
        workflow.status != StatusWorkflowSolicitacao.PENDENTE_VALIDACAO.value
        and not reversao_para_recusa
    ):
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail=(
                'A solicitação não está disponível para validação ou reversão.'
            ),
        )
    if payload.decisao == DecisaoValidacaoSolicitacao.VALIDADA and (
        solicitacao.valor_nota is None or solicitacao.valor_nota <= 0
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
        filtros.append(SolicitacaoNota.nm_paciente.ilike(f'%{nome_paciente}%'))
    if cpf := _texto(filtros_query.cpf):
        filtros.append(SolicitacaoNota.nr_cpf.ilike(f'%{cpf}%'))
    if tipo_atendimento := _texto(filtros_query.tipo_atendimento):
        filtros.append(SolicitacaoNota.tipo_atendimento == tipo_atendimento)
    if local := _texto(filtros_query.local):
        filtros.append(SolicitacaoNota.local == local)
    if cnpj_emissor := _texto(filtros_query.cnpj_emissor):
        filtros.append(SolicitacaoNota.cnpj_emissor == cnpj_emissor)

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
            SolicitacaoNotaWorkflow.solicitacao_nota_id == SolicitacaoNota.id,
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
    total = (
        session_postgres.scalar(
            select(func.count()).select_from(base_query.subquery())
        )
        or 0
    )
    rows = session_postgres.execute(
        base_query
        .order_by(
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
            {
                emissao.solicitacao_nota_id: emissao.cnpj_emissor
                for emissao in emissoes
                if emissao.cnpj_emissor
            },
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
        arquivo.nome_arquivo
        .replace('\\', '/')
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
                status=(StatusWorkflowSolicitacao.PENDENTE_VALIDACAO.value),
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
