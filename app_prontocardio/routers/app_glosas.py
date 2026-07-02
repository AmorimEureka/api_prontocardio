from datetime import datetime
from http import HTTPStatus
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app_prontocardio.database import get_session_oracle, get_session_postgres
from app_prontocardio.models import (
    ModelContaAtendimento,
    ModelConvenio,
    PrazoRecursoConvenio,
    RegistroGlosa,
    TipoAtendimento,
    Tiss,
    Usuario,
)
from app_prontocardio.schema import (
    Atendimento,
    Atendimentos,
    ConvenioList,
    FilterSearch,
    Message,
    PrazoRecursoConvenioInput,
    PrazoRecursoConvenioList,
    RegistroGlosaCreate,
    RegistroGlosaPublic,
    RegistroGlosaRecebimentoUpdate,
    RegistroGlosas,
    TissList,
)
from app_prontocardio.security import valida_token_usuario_atual

router = APIRouter(prefix='/app_glosas', tags=['app_glosas'])

ValidaUsuarioAtual = Annotated[Usuario, Depends(valida_token_usuario_atual)]
SessionPostgres = Annotated[Session, Depends(get_session_postgres)]
TEXT_FILTER_FIELDS = {'nm_paciente', 'nm_convenio', 'descricao'}


def _is_oracle_connect_timeout(exc: SQLAlchemyError) -> bool:
    error_texts = [str(exc)]

    orig_error = getattr(exc, 'orig', None)
    if orig_error is not None:
        error_texts.append(str(orig_error))

    cause = exc.__cause__
    if cause is not None:
        error_texts.append(str(cause))

    return any('ORA-12170' in text for text in error_texts)


def _get_registro_glosa_or_404(
    glosa_id: int,
    session: Session,
) -> RegistroGlosa:
    registro_glosa = session.get(RegistroGlosa, glosa_id)
    if registro_glosa is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Registro de glosa nao encontrado.',
        )

    return registro_glosa


def _data_criacao_sao_paulo():
    return datetime.now(ZoneInfo('America/Sao_Paulo')).replace(tzinfo=None)


def _aplicar_filtros_conta_atendimento(query, filtros: dict):
    for chave, valor in filtros.items():
        if hasattr(ModelContaAtendimento, chave):
            coluna = getattr(ModelContaAtendimento, chave)
            if chave == 'tp_atendimento':
                if isinstance(valor, TipoAtendimento):
                    query = query.where(coluna == valor.value)
                continue

            if chave in TEXT_FILTER_FIELDS and isinstance(valor, str):
                query = query.where(coluna.ilike(f'%{valor}%'))
            else:
                query = query.where(coluna == valor)

    return query


def _excluir_convenios_desabilitados(query, codigos_desabilitados):
    if codigos_desabilitados:
        return query.where(
            ModelContaAtendimento.cd_convenio.not_in(codigos_desabilitados)
        )
    return query


def _consultar_convenios_ativos_oracle(session_oracle: Session):
    return session_oracle.execute(
        select(
            ModelConvenio.cd_convenio,
            ModelConvenio.nm_convenio,
        )
        .where(ModelConvenio.sn_ativo == 'S')
        .where(ModelConvenio.nm_convenio.is_not(None))
        .order_by(ModelConvenio.nm_convenio)
    ).all()


@router.get(
    '/',
    status_code=HTTPStatus.OK,
    response_model=Atendimentos,
)
def conta_atendimento(
    usuario_atual: ValidaUsuarioAtual,
    campos_pesquisados: Annotated[FilterSearch, Depends()],
    session_postgres: SessionPostgres,
    tp_atendimento: TipoAtendimento = Query(
        default=None,
    ),
    session: Session = Depends(get_session_oracle),
):

    try:
        filtros = campos_pesquisados.model_dump(
            exclude_unset=True,
            exclude_none=True,
        )

        offset = filtros.pop('offset', campos_pesquisados.offset)
        limit = filtros.pop('limit', campos_pesquisados.limit)
        if tp_atendimento is not None:
            filtros['tp_atendimento'] = tp_atendimento
        if not filtros:
            raise HTTPException(
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                detail=(
                    'Informe pelo menos um criterio para realizar a pesquisa.'
                ),
            )

        codigos_desabilitados = tuple(
            session_postgres.scalars(
                select(PrazoRecursoConvenio.cd_convenio).where(
                    PrazoRecursoConvenio.habilitado.is_(False)
                )
            )
        )
        pacientes_filtrados = _excluir_convenios_desabilitados(
            _aplicar_filtros_conta_atendimento(
                select(ModelContaAtendimento.cd_paciente).group_by(
                    ModelContaAtendimento.cd_paciente
                ),
                filtros,
            ),
            codigos_desabilitados,
        ).subquery()
        total = (
            session.scalar(
                select(func.count()).select_from(pacientes_filtrados)
            )
            or 0
        )

        pacientes_query = _excluir_convenios_desabilitados(
            _aplicar_filtros_conta_atendimento(
                select(ModelContaAtendimento.cd_paciente)
                .group_by(ModelContaAtendimento.cd_paciente)
                .order_by(
                    func.min(ModelContaAtendimento.nm_paciente),
                    ModelContaAtendimento.cd_paciente,
                ),
                filtros,
            ),
            codigos_desabilitados,
        ).offset(offset)
        if limit is not None:
            pacientes_query = pacientes_query.limit(limit)

        pacientes_paginados = pacientes_query.subquery()

        query = _excluir_convenios_desabilitados(
            _aplicar_filtros_conta_atendimento(
                select(ModelContaAtendimento),
                filtros,
            ),
            codigos_desabilitados,
        ).where(
            ModelContaAtendimento.cd_paciente.in_(
                select(pacientes_paginados.c.cd_paciente)
            )
        )
        query = query.order_by(
            ModelContaAtendimento.nm_paciente,
            ModelContaAtendimento.cd_remessa,
            ModelContaAtendimento.cd_atendimento,
            ModelContaAtendimento.cd_reg,
            ModelContaAtendimento.cd_lancamento,
        )

        rows = session.execute(query).scalars().all()

    except SQLAlchemyError as exc:
        if _is_oracle_connect_timeout(exc):
            raise HTTPException(
                status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                detail='Banco Oracle indisponivel no momento.',
            ) from exc

        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail='Erro na consultar.',
        ) from exc

    if not rows:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Nenhum registro encontrado para os filtros informados.',
        )

    atendimentos_list = [
        Atendimento.model_validate(
            row,
            from_attributes=True,
        )
        for row in rows
    ]

    return {
        'atendimentos': atendimentos_list,
        'total': total,
        'limit': limit,
        'offset': offset,
    }


@router.get(
    '/glosas',
    status_code=HTTPStatus.OK,
    response_model=RegistroGlosas,
)
def consultar_glosas_registradas(
    usuario_atual: ValidaUsuarioAtual,
    campos_pesquisados: Annotated[FilterSearch, Depends()],
    session: SessionPostgres,
    tp_atendimento: TipoAtendimento = Query(
        default=None,
    ),
    incluir_inativos: bool = Query(default=False),
):
    filtros = campos_pesquisados.model_dump(
        exclude_unset=True,
        exclude_none=True,
    )

    offset = filtros.pop('offset', campos_pesquisados.offset)
    limit = filtros.pop('limit', campos_pesquisados.limit)
    if tp_atendimento is not None:
        filtros['tp_atendimento'] = tp_atendimento

    query = select(RegistroGlosa)
    if not incluir_inativos:
        query = query.where(RegistroGlosa.sn_ativo == 'true')
    query = query.where(
        ~select(PrazoRecursoConvenio.id)
        .where(
            PrazoRecursoConvenio.cd_convenio == RegistroGlosa.cd_convenio,
            PrazoRecursoConvenio.habilitado.is_(False),
        )
        .exists()
    )

    field_mapping = {
        'cd_remessa': RegistroGlosa.cd_remessa,
        'cd_atendimento': RegistroGlosa.cd_atendimento,
        'cd_reg': RegistroGlosa.conta,
        'nm_convenio': RegistroGlosa.convenio,
        'nm_paciente': RegistroGlosa.nm_paciente,
        'descricao': RegistroGlosa.descricao_glosa,
        'tp_atendimento': RegistroGlosa.tp_atendimento,
    }
    text_fields = {'nm_convenio', 'nm_paciente', 'descricao', 'tp_atendimento'}

    for chave, valor in filtros.items():
        coluna = field_mapping.get(chave)
        if coluna is not None:
            if chave == 'tp_atendimento':
                if isinstance(valor, TipoAtendimento):
                    query = query.where(coluna == valor.value)
                continue

            if chave in text_fields and isinstance(valor, str):
                query = query.where(coluna.ilike(f'%{valor}%'))
            else:
                query = query.where(coluna == valor)
            continue

        if chave == 'nm_paciente' and isinstance(valor, str):
            query = query.where(
                cast(RegistroGlosa.codigo_paciente, String).ilike(f'%{valor}%')
            )

    rows = (
        session
        .execute(
            query.order_by(RegistroGlosa.id.desc()).offset(offset).limit(limit)
        )
        .scalars()
        .all()
    )

    return {'glosas': rows}


@router.get(
    '/convenios',
    status_code=HTTPStatus.OK,
    response_model=ConvenioList,
)
def consultar_convenios(
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
    session_oracle: Session = Depends(get_session_oracle),
):
    try:
        convenios = _consultar_convenios_ativos_oracle(session_oracle)
    except SQLAlchemyError as exc:
        if _is_oracle_connect_timeout(exc):
            raise HTTPException(
                status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                detail='Banco Oracle indisponivel no momento.',
            ) from exc
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail='Erro ao consultar convenios.',
        ) from exc

    codigos_desabilitados = set(
        session_postgres.scalars(
            select(PrazoRecursoConvenio.cd_convenio).where(
                PrazoRecursoConvenio.habilitado.is_(False)
            )
        )
    )
    return {
        'convenios': [
            {
                'cd_convenio': cd_convenio,
                'nm_convenio': nm_convenio,
            }
            for cd_convenio, nm_convenio in convenios
            if cd_convenio not in codigos_desabilitados
        ]
    }


@router.get(
    '/prazos-recurso-convenio',
    status_code=HTTPStatus.OK,
    response_model=PrazoRecursoConvenioList,
)
def consultar_prazos_recurso_convenio(
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
    session_oracle: Session = Depends(get_session_oracle),
):
    convenios = []

    try:
        convenios = _consultar_convenios_ativos_oracle(session_oracle)
    except SQLAlchemyError as exc:
        if not _is_oracle_connect_timeout(exc):
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail='Erro ao consultar convenios.',
            ) from exc

    prazos = {
        prazo.cd_convenio: prazo
        for prazo in session_postgres.execute(
            select(PrazoRecursoConvenio)
        ).scalars()
    }

    rows = []
    usados = set()
    for cd_convenio, nm_convenio in convenios:
        prazo = prazos.get(cd_convenio)
        usados.add(cd_convenio)
        rows.append({
            'cd_convenio': cd_convenio,
            'convenio': nm_convenio,
            'dias_para_recurso': (
                prazo.dias_para_recurso if prazo is not None else None
            ),
            'configurado': prazo is not None,
            'habilitado': prazo.habilitado if prazo is not None else True,
        })

    for prazo in sorted(
        (item for key, item in prazos.items() if key not in usados),
        key=lambda item: item.convenio,
    ):
        rows.append({
            'cd_convenio': prazo.cd_convenio,
            'convenio': prazo.convenio,
            'dias_para_recurso': prazo.dias_para_recurso,
            'configurado': True,
            'habilitado': prazo.habilitado,
        })

    return {'convenios': rows}


@router.get(
    '/tiss',
    status_code=HTTPStatus.OK,
    response_model=TissList,
)
def consultar_tiss(
    usuario_atual: ValidaUsuarioAtual,
    session: SessionPostgres,
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=600),
):
    query = select(Tiss)

    if q:
        termo = f'%{q.strip()}%'
        query = query.where(
            Tiss.codigo_termo.ilike(termo) | Tiss.termo.ilike(termo)
        )

    rows = (
        session
        .execute(query.order_by(Tiss.codigo_termo).limit(limit))
        .scalars()
        .all()
    )

    return {'itens': rows}


@router.post(
    '/glosas',
    status_code=HTTPStatus.CREATED,
    response_model=RegistroGlosaPublic,
)
def registrar_glosa(
    payload: RegistroGlosaCreate,
    usuario_atual: ValidaUsuarioAtual,
    session: SessionPostgres,
):
    registro_glosa = RegistroGlosa(
        **payload.model_dump(),
        sn_ativo='true',
    )
    registro_glosa.data_criacao = _data_criacao_sao_paulo()

    session.add(registro_glosa)
    session.commit()
    session.refresh(registro_glosa)

    return registro_glosa


@router.put(
    '/prazos-recurso-convenio',
    status_code=HTTPStatus.OK,
    response_model=PrazoRecursoConvenioList,
)
def salvar_prazos_recurso_convenio(
    payload: list[PrazoRecursoConvenioInput],
    usuario_atual: ValidaUsuarioAtual,
    session: SessionPostgres,
):
    codigos = {item.cd_convenio for item in payload}
    prazos = {
        prazo.cd_convenio: prazo
        for prazo in session.scalars(
            select(PrazoRecursoConvenio).where(
                PrazoRecursoConvenio.cd_convenio.in_(codigos)
            )
        )
    }
    data_atualizacao = _data_criacao_sao_paulo()

    for item in payload:
        prazo = prazos.get(item.cd_convenio)
        if prazo is None:
            prazo = PrazoRecursoConvenio(**item.model_dump())
            session.add(prazo)
            prazos[item.cd_convenio] = prazo
        else:
            prazo.convenio = item.convenio
            prazo.dias_para_recurso = item.dias_para_recurso
            prazo.habilitado = item.habilitado
            prazo.data_atualizacao = data_atualizacao

    session.commit()

    rows = (
        session
        .execute(
            select(PrazoRecursoConvenio).order_by(
                PrazoRecursoConvenio.convenio
            )
        )
        .scalars()
        .all()
    )
    return {
        'convenios': [
            {
                'cd_convenio': row.cd_convenio,
                'convenio': row.convenio,
                'dias_para_recurso': row.dias_para_recurso,
                'configurado': True,
                'habilitado': row.habilitado,
            }
            for row in rows
        ]
    }


@router.put(
    '/glosas/{glosa_id}',
    status_code=HTTPStatus.OK,
    response_model=RegistroGlosaPublic,
)
def editar_glosa(
    glosa_id: int,
    payload: RegistroGlosaCreate,
    usuario_atual: ValidaUsuarioAtual,
    session: SessionPostgres,
):
    registro_glosa = _get_registro_glosa_or_404(glosa_id, session)

    for field_name, value in payload.model_dump().items():
        setattr(registro_glosa, field_name, value)
    registro_glosa.sn_ativo = 'true'
    registro_glosa.data_criacao = _data_criacao_sao_paulo()

    session.commit()
    session.refresh(registro_glosa)

    return registro_glosa


@router.patch(
    '/glosas/{glosa_id}/recebimento',
    status_code=HTTPStatus.OK,
    response_model=RegistroGlosaPublic,
)
def registrar_recebimento_glosa(
    glosa_id: int,
    payload: RegistroGlosaRecebimentoUpdate,
    usuario_atual: ValidaUsuarioAtual,
    session: SessionPostgres,
):
    registro_glosa = _get_registro_glosa_or_404(glosa_id, session)
    if registro_glosa.sn_glosado != 'true':
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='Recebimento permitido apenas para recursos de glosa.',
        )
    valor_recursado = registro_glosa.valor_glosado or registro_glosa.valor
    qtd_recursada = registro_glosa.qtd_glosada or 1
    if payload.valor_recebido > valor_recursado:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='O valor recebido nao pode exceder o valor recursado.',
        )
    if payload.qtd_recebida > qtd_recursada:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=
            'A quantidade recebida nao pode exceder a quantidade recursada.',
        )

    for field_name, value in payload.model_dump().items():
        setattr(registro_glosa, field_name, value)
    registro_glosa.data_criacao = _data_criacao_sao_paulo()

    session.commit()
    session.refresh(registro_glosa)

    return registro_glosa


@router.delete(
    '/glosas/{glosa_id}',
    status_code=HTTPStatus.OK,
    response_model=Message,
)
def deletar_glosa(
    glosa_id: int,
    usuario_atual: ValidaUsuarioAtual,
    session: SessionPostgres,
):
    registro_glosa = _get_registro_glosa_or_404(glosa_id, session)

    registro_glosa.sn_ativo = 'not'
    registro_glosa.data_criacao = _data_criacao_sao_paulo()
    session.commit()

    return {'message': 'Registro de glosa desfeito!'}
