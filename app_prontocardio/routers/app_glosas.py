from datetime import datetime
from decimal import Decimal
from http import HTTPStatus
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, false, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app_prontocardio.database import get_session_oracle, get_session_postgres
from app_prontocardio.models import (
    ConciliacaoFaturamento,
    ConciliacaoFaturamentoRemessa,
    ModelContaAtendimento,
    ModelConvenio,
    ModelHpcPaciente,
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
ORACLE_IN_MAX_VALUES = 1000


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


def _registros_da_glosa_conciliada(
    registro_glosa: RegistroGlosa,
    session: Session,
) -> tuple[ConciliacaoFaturamentoRemessa, list[RegistroGlosa]] | None:
    if registro_glosa.conciliacao_remessa_id is None:
        return None
    conciliacao_remessa = session.get(
        ConciliacaoFaturamentoRemessa,
        registro_glosa.conciliacao_remessa_id,
    )
    if conciliacao_remessa is None:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='A origem da glosa na conciliacao nao foi encontrada.',
        )
    registros = session.scalars(
        select(RegistroGlosa).where(
            RegistroGlosa.conciliacao_remessa_id
            == registro_glosa.conciliacao_remessa_id
        )
    ).all()
    return conciliacao_remessa, registros


def _validar_alocacao_glosa_conciliada(
    registro_origem: RegistroGlosa,
    payload: RegistroGlosaCreate,
    session: Session,
    registro_substituido_id: int | None = None,
) -> tuple[list[RegistroGlosa], Decimal] | None:
    origem = _registros_da_glosa_conciliada(registro_origem, session)
    if origem is None:
        return None
    conciliacao_remessa, registros = origem
    if (
        payload.cd_remessa != registro_origem.cd_remessa
        or payload.conta != registro_origem.conta
        or payload.cd_lancamento != registro_origem.cd_lancamento
    ):
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                'Remessa, conta e lancamento do item de origem nao podem '
                'ser alterados.'
            ),
        )

    valor_outros_itens = sum(
        (
            item.valor_recursado
            for item in registros
            if item.id != registro_substituido_id
            and item.sn_ativo == 'true'
            and item.valor_recursado is not None
        ),
        start=Decimal('0.00'),
    )
    valor_alocado = valor_outros_itens + (
        payload.valor_recursado or Decimal('0.00')
    )
    if valor_alocado > conciliacao_remessa.valor_glosado:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                'A soma dos itens tratados nao pode exceder o valor glosado '
                'da remessa na conciliacao.'
            ),
        )
    return registros, valor_alocado


def _registros_do_mesmo_item(
    registro_origem: RegistroGlosa,
    session: Session,
) -> list[RegistroGlosa]:
    filtros = [
        RegistroGlosa.cd_remessa == registro_origem.cd_remessa,
        RegistroGlosa.cd_atendimento == registro_origem.cd_atendimento,
        RegistroGlosa.conta == registro_origem.conta,
    ]
    if registro_origem.cd_lancamento is None:
        filtros.append(RegistroGlosa.cd_lancamento.is_(None))
    else:
        filtros.append(
            RegistroGlosa.cd_lancamento == registro_origem.cd_lancamento
        )
    if registro_origem.conciliacao_remessa_id is None:
        filtros.append(RegistroGlosa.conciliacao_remessa_id.is_(None))
    else:
        filtros.append(
            RegistroGlosa.conciliacao_remessa_id
            == registro_origem.conciliacao_remessa_id
        )
    if registro_origem.motivo_glosa is None:
        filtros.append(RegistroGlosa.motivo_glosa.is_(None))
    else:
        filtros.append(
            RegistroGlosa.motivo_glosa == registro_origem.motivo_glosa
        )
    return session.scalars(
        select(RegistroGlosa)
        .where(*filtros)
        .order_by(RegistroGlosa.id)
    ).all()


def _resolver_registro_tratativa(
    registro_origem: RegistroGlosa,
    payload: RegistroGlosaCreate,
    registros_item: list[RegistroGlosa],
) -> RegistroGlosa | None:
    if (
        registro_origem.sn_glosado == payload.sn_glosado
        and registro_origem.sn_ativo == 'true'
    ):
        return registro_origem

    candidatos = [
        registro
        for registro in registros_item
        if registro.sn_glosado == payload.sn_glosado
        and registro.id != registro_origem.id
    ]
    if not candidatos:
        return None
    return next(
        (
            registro
            for registro in candidatos
            if registro.sn_ativo == 'true'
        ),
        candidatos[-1],
    )


def _validar_limites_tratativas_item(
    registro_destino: RegistroGlosa | None,
    payload: RegistroGlosaCreate,
    registros_item: list[RegistroGlosa],
) -> None:
    destino_id = registro_destino.id if registro_destino is not None else None
    registros_ativos = [
        registro
        for registro in registros_item
        if registro.id != destino_id
        and registro.sn_ativo == 'true'
        and registro.status_tratativa != 'pendente'
    ]
    quantidade_total = sum(
        (
            registro.qtd_recursado or Decimal('0.00')
            for registro in registros_ativos
        ),
        start=payload.qtd_recursado or Decimal('0.00'),
    )
    valor_total = sum(
        (
            registro.valor_recursado or Decimal('0.00')
            for registro in registros_ativos
        ),
        start=payload.valor_recursado or Decimal('0.00'),
    )
    if quantidade_total > payload.qtd_registro:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                'A soma das quantidades recursada e acatada nao pode '
                'ultrapassar a quantidade do item.'
            ),
        )
    if valor_total > payload.valor:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                'A soma dos valores recursado e acatado nao pode '
                'ultrapassar o valor do item.'
            ),
        )


def _sincronizar_itens_pendentes_glosa(
    registros: list[RegistroGlosa],
    valor_alocado: Decimal,
    valor_glosado: Decimal,
) -> None:
    tratativa_concluida = valor_alocado == valor_glosado
    for registro in registros:
        if (
            registro.valor_recursado is None
            and registro.processo_recurso is None
            and registro.dt_recurso is None
        ):
            registro.sn_ativo = 'not' if tratativa_concluida else 'true'


def _desfazer_tratativa_glosa_conciliada(
    registro_glosa: RegistroGlosa,
    conciliacao_remessa: ConciliacaoFaturamentoRemessa,
    session: Session,
) -> None:
    if any(
        value is not None
        for value in (
            registro_glosa.dt_recebimento,
            registro_glosa.valor_recebido,
            registro_glosa.qtd_recebida,
            registro_glosa.observacao_recebimento,
        )
    ):
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                'Nao e possivel desfazer uma glosa com recebimento '
                'registrado.'
            ),
        )

    conciliacao = session.get(
        ConciliacaoFaturamento,
        conciliacao_remessa.conciliacao_id,
    )
    descricao_item = str(
        registro_glosa.descricao_item
        or registro_glosa.procedimento
        or 'Item da remessa'
    ).strip()

    registro_glosa.processo_recurso = None
    registro_glosa.qtd_recursado = None
    registro_glosa.valor_recursado = None
    registro_glosa.dt_recurso = None
    registro_glosa.dt_pagamento = (
        conciliacao.data_recebimento if conciliacao is not None else None
    )
    registro_glosa.descricao_glosa = (
        f'{descricao_item}. Pendente de tratativa da NFS-e '
        f'{conciliacao.numero_nfse if conciliacao is not None else "-"}.'
    )
    registro_glosa.sn_glosado = 'true'
    registro_glosa.sn_ativo = 'true'


def _aplicar_filtros_conta_atendimento(query, filtros: dict):
    for chave, valor in filtros.items():
        if hasattr(ModelContaAtendimento, chave):
            coluna = getattr(ModelContaAtendimento, chave)
            if chave == 'tp_atendimento':
                if isinstance(valor, TipoAtendimento):
                    query = query.where(coluna == valor.value)
                continue

            if chave == 'cd_paciente' and isinstance(valor, tuple):
                if not valor:
                    query = query.where(false())
                    continue

                grupos = (
                    valor[indice : indice + ORACLE_IN_MAX_VALUES]
                    for indice in range(0, len(valor), ORACLE_IN_MAX_VALUES)
                )
                query = query.where(
                    or_(*(coluna.in_(grupo) for grupo in grupos))
                )
                continue

            if chave in TEXT_FILTER_FIELDS and isinstance(valor, str):
                query = query.where(coluna.ilike(f'%{valor}%'))
            else:
                query = query.where(coluna == valor)

    return query


def _resolver_filtro_nome_paciente(
    session: Session,
    filtros: dict,
) -> dict:
    filtros_resolvidos = dict(filtros)
    nome_paciente = filtros_resolvidos.pop('nm_paciente', None)
    if not isinstance(nome_paciente, str):
        return filtros_resolvidos

    codigos_paciente = tuple(
        session.scalars(
            select(ModelHpcPaciente.cd_paciente).where(
                ModelHpcPaciente.paciente.ilike(f'%{nome_paciente}%')
            )
        )
    )
    filtros_resolvidos['cd_paciente'] = codigos_paciente
    return filtros_resolvidos


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

        filtros = _resolver_filtro_nome_paciente(session, filtros)

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

    query = select(RegistroGlosa).options(
        selectinload(RegistroGlosa.conciliacao_remessa).selectinload(
            ConciliacaoFaturamentoRemessa.registros_glosa
        )
    )
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
    _validar_limites_tratativas_item(
        None,
        payload,
        _registros_do_mesmo_item(registro_glosa, session),
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
    registro_origem = _get_registro_glosa_or_404(glosa_id, session)
    registros_item = _registros_do_mesmo_item(registro_origem, session)
    registro_glosa = _resolver_registro_tratativa(
        registro_origem,
        payload,
        registros_item,
    )
    _validar_limites_tratativas_item(
        registro_glosa,
        payload,
        registros_item,
    )
    alocacao = _validar_alocacao_glosa_conciliada(
        registro_origem,
        payload,
        session,
        registro_glosa.id if registro_glosa is not None else None,
    )

    if registro_glosa is None:
        registro_glosa = RegistroGlosa(
            **payload.model_dump(),
            conciliacao_remessa_id=registro_origem.conciliacao_remessa_id,
            origem_registro=registro_origem.origem_registro,
            sn_ativo='true',
        )
        session.add(registro_glosa)
        if alocacao is not None:
            alocacao[0].append(registro_glosa)
    else:
        for field_name, value in payload.model_dump().items():
            setattr(registro_glosa, field_name, value)
    registro_glosa.sn_ativo = 'true'
    registro_glosa.data_criacao = _data_criacao_sao_paulo()
    if alocacao is not None:
        registros, valor_alocado = alocacao
        conciliacao_remessa = registro_origem.conciliacao_remessa
        if conciliacao_remessa is None:
            conciliacao_remessa = session.get(
                ConciliacaoFaturamentoRemessa,
                registro_origem.conciliacao_remessa_id,
            )
        _sincronizar_itens_pendentes_glosa(
            registros,
            valor_alocado,
            conciliacao_remessa.valor_glosado,
        )

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
    today = datetime.now(ZoneInfo('America/Sao_Paulo')).date()
    if payload.dt_recebimento > today:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                'A data do recebimento nao pode ser maior que a data atual.'
            ),
        )
    if (
        registro_glosa.dt_recurso is not None
        and payload.dt_recebimento < registro_glosa.dt_recurso
    ):
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='A data do recebimento nao pode ser anterior ao recurso.',
        )
    if (
        registro_glosa.dt_recebimento is not None
        and payload.dt_recebimento < registro_glosa.dt_recebimento
    ):
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                'A data do recebimento acumulado nao pode ser anterior '
                'a data ja registrada.'
            ),
        )
    valor_recursado = registro_glosa.valor_recursado or registro_glosa.valor
    qtd_recursada = registro_glosa.qtd_recursado or 1
    if (
        registro_glosa.valor_recebido is not None
        and payload.valor_recebido < registro_glosa.valor_recebido
    ):
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                'O valor recebido acumulado nao pode ser menor que o valor '
                'ja registrado.'
            ),
        )
    if (
        registro_glosa.qtd_recebida is not None
        and payload.qtd_recebida < registro_glosa.qtd_recebida
    ):
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                'A quantidade recebida acumulada nao pode ser menor que a '
                'quantidade ja registrada.'
            ),
        )
    if payload.valor_recebido > valor_recursado:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='O valor recebido nao pode exceder o valor recursado.',
        )
    if payload.qtd_recebida > qtd_recursada:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                'A quantidade recebida nao pode exceder a quantidade '
                'recursada.'
            ),
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

    origem = _registros_da_glosa_conciliada(registro_glosa, session)
    if origem is not None:
        conciliacao_remessa, registros = origem
        possui_outra_tratativa = any(
            item.id != registro_glosa.id
            and item.sn_ativo == 'true'
            and item.conta == registro_glosa.conta
            and item.cd_lancamento == registro_glosa.cd_lancamento
            for item in registros
        )
        if registro_glosa.sn_glosado == 'not' and possui_outra_tratativa:
            registro_glosa.sn_ativo = 'not'
        else:
            _desfazer_tratativa_glosa_conciliada(
                registro_glosa,
                conciliacao_remessa,
                session,
            )
        valor_alocado = sum(
            (
                item.valor_recursado
                for item in registros
                if item.id != registro_glosa.id
                and item.sn_ativo == 'true'
                and item.valor_recursado is not None
            ),
            start=Decimal('0.00'),
        )
        _sincronizar_itens_pendentes_glosa(
            registros,
            valor_alocado,
            conciliacao_remessa.valor_glosado,
        )
    else:
        registro_glosa.sn_ativo = 'not'
    registro_glosa.data_criacao = _data_criacao_sao_paulo()
    session.commit()

    return {'message': 'Registro de glosa desfeito!'}
