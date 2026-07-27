from datetime import date, datetime, timedelta
from decimal import Decimal
from http import HTTPStatus
import threading

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app_prontocardio.database import get_session_oracle

router = APIRouter(prefix='/farmacia', tags=['farmacia'])

FARMACIA_ESTOQUE_ID = 2

_cache_lock = threading.Lock()
_last_rows: list[dict] = []
_last_success_at: datetime | None = None


def _now_iso() -> str:
    return datetime.now().isoformat()


def _first_value(row: dict, *names: str, default=None):
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
        upper = name.upper()
        if upper in row and row[upper] is not None:
            return row[upper]
        lower = name.lower()
        if lower in row and row[lower] is not None:
            return row[lower]

    return default


def _bool_from_db(value) -> bool:
    if value is True or value == 1:
        return True

    return str(value or '').strip().upper() in {
        'S',
        'SIM',
        'Y',
        'YES',
        'TRUE',
        '1',
    }


def _code_value(value) -> str:
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return str(int(value))

    if isinstance(value, float) and value.is_integer():
        return str(int(value))

    return str(value or '').strip()


def _text_value(value) -> str:
    return str(value or '').strip()


def _float_value(value) -> float:
    if value is None:
        return 0

    return float(value)


def _date_to_iso(value) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time()).isoformat()

    if value:
        return str(value)

    return None


def _minutes_ago(value) -> int:
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, datetime.min.time())

    if not isinstance(value, datetime):
        return 0

    delta = datetime.now() - value.replace(tzinfo=None)
    return max(0, round(delta.total_seconds() / 60))


def _normalize_status(value) -> str:
    status = str(value or '').strip().upper()
    if status in {'CANCELADA', 'CANCELADO', 'CANCELADO(A)', 'A', 'X'}:
        return 'Cancelada'
    if status in {'ATENDIDA', 'ATENDIDO', 'S', 'T', 'TOTAL'}:
        return 'Atendida'
    if status in {'PARCIAL', 'P'}:
        return 'Parcial'

    return 'Pendente'


def _cancellation_reason(row: dict) -> str:
    code = _code_value(
        _first_value(row, 'codigo_motivo_cancelamento', default='')
    )
    description = _text_value(
        _first_value(row, 'motivo_cancelamento', default='')
    )
    justification = _text_value(
        _first_value(row, 'justificativa_cancelamento', default='')
    )

    main_reason = description
    if code and description and not description.startswith(f'{code} - '):
        main_reason = f'{code} - {description}'
    elif code and not description:
        main_reason = f'Codigo {code}'

    pieces = []
    for value in (main_reason, justification):
        clean = _text_value(value)
        if clean and clean not in pieces:
            pieces.append(clean)

    return ' - '.join(pieces)


def _normalize_row(row) -> dict:
    row = dict(row)
    data_solicitacao = _first_value(row, 'data_solicitacao')
    data_cancelamento = _first_value(row, 'data_cancelamento')
    paciente = _first_value(row, 'paciente', default='')

    return {
        'solicitacao': _first_value(row, 'solicitacao'),
        'atendimento': _first_value(row, 'atendimento'),
        'setor': _first_value(row, 'setor', default='SETOR NAO INFORMADO'),
        'centroCustoSolicitante': _first_value(
            row, 'centro_custo_solicitante', default=''
        ),
        'setorPorCentroCusto': _bool_from_db(
            _first_value(row, 'setor_por_centro_custo', default='N')
        ),
        'leito': _first_value(row, 'leito', default=''),
        'unidadeInternacao': _first_value(
            row, 'unidade_internacao', default=''
        ),
        'setorInternacao': _first_value(row, 'setor_internacao', default=''),
        'paciente': paciente or 'REPOSIÇÃO AUTOMÁTICA / COTAS',
        'prescricao': _first_value(row, 'prescricao', default=''),
        'dataSolicitacao': _date_to_iso(data_solicitacao),
        'dataCancelamento': _date_to_iso(data_cancelamento),
        'codigoMotivoCancelamento': _code_value(
            _first_value(row, 'codigo_motivo_cancelamento', default='')
        ),
        'descricaoMotivoCancelamento': _text_value(
            _first_value(row, 'motivo_cancelamento', default='')
        ),
        'justificativaCancelamento': _text_value(
            _first_value(row, 'justificativa_cancelamento', default='')
        ),
        'motivoCancelamento': _cancellation_reason(row),
        'minutesAgo': int(
            _first_value(
                row,
                'tempo_minutos',
                default=_minutes_ago(data_solicitacao),
            )
            or 0
        ),
        'status': _normalize_status(_first_value(row, 'status')),
        'urgent': _bool_from_db(_first_value(row, 'urgente')),
        'allergy': _bool_from_db(_first_value(row, 'alergia')),
        'items': [],
    }


def _table_columns(session: Session, table_name: str) -> dict[str, set[str]]:
    rows = (
        session.execute(
            text(
                """
                select owner, column_name
                  from all_tab_columns
                 where table_name = :table_name
                """
            ),
            {'table_name': table_name.upper()},
        )
        .mappings()
        .all()
    )

    owners: dict[str, set[str]] = {}
    for row in rows:
        owners.setdefault(row['owner'], set()).add(row['column_name'])

    return owners


def _resolve_table(
    session: Session,
    candidates: tuple[str, ...],
    required_columns: tuple[str, ...],
) -> tuple[str, set[str]] | None:
    required = {column.upper() for column in required_columns}
    for table_name in candidates:
        owners = _table_columns(session, table_name)
        ordered_owners = sorted(
            owners,
            key=lambda owner: 0 if owner.upper() == 'DBAMV' else 1,
        )
        for owner in ordered_owners:
            columns = owners[owner]
            if required.issubset(columns):
                return f'{owner}.{table_name.upper()}', columns

    return None


def _resolve_cancel_join(session: Session) -> tuple[str, str, str]:
    resolved = _resolve_table(
        session,
        (
            'MOTIVO_CANC_SOL',
            'M_CANCSOL',
            'MOT_CANC_SOL',
            'MOTIVO_CANCELAMENTO_SOL',
        ),
        ('CD_MOTIVO_CANC',),
    )
    if not resolved:
        return (
            '',
            'cast(null as varchar2(255)) as motivo_cancelamento',
            'cast(null as varchar2(255))',
        )

    table_ref, columns = resolved
    desc_column = next(
        (
            column
            for column in (
                'DS_MOTIVO_CANC',
                'DS_MOTIVO_CANCELAMENTO',
                'DS_MOTIVO',
                'DESCRICAO',
            )
            if column in columns
        ),
        None,
    )
    if not desc_column:
        return (
            '',
            'cast(null as varchar2(255)) as motivo_cancelamento',
            'cast(null as varchar2(255))',
        )

    return (
        f'left join {table_ref} mcs '
        'on mcs.cd_motivo_canc = sp.cd_motivo_canc',
        f'mcs.{desc_column} as motivo_cancelamento',
        f'mcs.{desc_column}',
    )


def _resolve_solsai_optional_columns(session: Session) -> dict[str, str]:
    owners = _table_columns(session, 'SOLSAI_PRO')
    columns = set().union(*owners.values()) if owners else set()

    return {
        'justificativa': (
            'sp.ds_justificativa_cancelamento'
            if 'DS_JUSTIFICATIVA_CANCELAMENTO' in columns
            else 'cast(null as varchar2(4000))'
        )
    }


def _query_items(session: Session, solicitacoes: list) -> dict[str, list[dict]]:
    keys = [value for value in solicitacoes if value is not None]
    if not keys:
        return {}

    binds = {f's{index}': value for index, value in enumerate(keys)}
    placeholders = ', '.join(f':s{index}' for index in range(len(keys)))
    sql = f"""
        with produto_substancia as (
          select cd_produto, cd_substancia
            from dbamv.subs_pro
           where cd_substancia is not null
          union
          select cd_produto, cd_substancia_principal as cd_substancia
            from dbamv.produto
           where cd_substancia_principal is not null
        ),
        alergia_substancia as (
          select distinct cd_paciente, cd_substancia
            from dbamv.pw_doc_alergia_pac
           where nvl(sn_ativo, 'S') = 'S'
             and cd_substancia is not null
        )
        select
            isp.cd_itsolsai_pro as item_id,
            isp.cd_solsai_pro as solicitacao,
            isp.cd_produto as produto_id,
            pro.ds_produto as nome,
            nvl(isp.qt_solicitado, 0) as solicitado,
            nvl(isp.qt_atendida, 0) as atendido,
            nvl(up.ds_unidade, '') as unidade,
            case
              when nvl(isp.qt_atendida, 0) >= nvl(isp.qt_solicitado, 0)
                then 'Atendida'
              when nvl(isp.qt_atendida, 0) > 0 then 'Parcial'
              else 'Pendente'
            end as status,
            ps.cd_substancia,
            sub.ds_substancia,
            case when al.cd_substancia is not null then 'S' else 'N' end
              as alergia_item
          from dbamv.itsolsai_pro isp
          left join dbamv.solsai_pro sp
            on sp.cd_solsai_pro = isp.cd_solsai_pro
          left join dbamv.atendime atd
            on atd.cd_atendimento = sp.cd_atendimento
          left join dbamv.produto pro on pro.cd_produto = isp.cd_produto
          left join dbamv.uni_pro up on up.cd_uni_pro = isp.cd_uni_pro
          left join produto_substancia ps on ps.cd_produto = isp.cd_produto
          left join dbamv.substancia sub
            on sub.cd_substancia = ps.cd_substancia
          left join alergia_substancia al
            on al.cd_paciente = atd.cd_paciente
           and al.cd_substancia = ps.cd_substancia
         where isp.cd_solsai_pro in ({placeholders})
         order by isp.cd_solsai_pro, pro.ds_produto, sub.ds_substancia
    """
    rows = session.execute(text(sql), binds).mappings().all()
    items_by_solicitacao: dict[str, list[dict]] = {}
    items_by_key: dict[str, dict] = {}
    for row in rows:
        solicitacao_key = str(_first_value(row, 'solicitacao'))
        item_id = _first_value(row, 'item_id')
        product_id = _first_value(row, 'produto_id')
        item_name = _first_value(row, 'nome', default='Item solicitado')
        item_key = str(
            item_id
            or f'{_first_value(row, "solicitacao")}:{product_id}:{item_name}'
        )

        if item_key not in items_by_key:
            item = {
                'nome': item_name or 'Item solicitado',
                'solicitado': _float_value(
                    _first_value(row, 'solicitado', default=0)
                ),
                'atendido': _float_value(
                    _first_value(row, 'atendido', default=0)
                ),
                'unidade': _first_value(row, 'unidade', default='') or '',
                'status': _normalize_status(_first_value(row, 'status')),
                'obs': '',
                'allergy': False,
                'allergySubstance': None,
            }
            items_by_key[item_key] = item
            items_by_solicitacao.setdefault(solicitacao_key, []).append(item)

        item = items_by_key[item_key]
        if _first_value(row, 'alergia_item') == 'S':
            item['allergy'] = True
            substance = _first_value(row, 'ds_substancia')
            substance = substance or f'Substancia {_first_value(row, "cd_substancia")}'
            current = item.get('allergySubstance')
            substances = (
                []
                if not current
                else [value.strip() for value in current.split(',')]
            )
            if substance not in substances:
                substances.append(substance)
            item['allergySubstance'] = ', '.join(substances)
            item['obs'] = f'Alergia relacionada: {item["allergySubstance"]}'

    return items_by_solicitacao


def _query_soulmv(
    session: Session,
    max_rows: int,
    include_atendidas: bool,
    date_from: date,
    date_to: date,
    search: str = '',
) -> list[dict]:
    max_rows = min(1000, max(1, max_rows))
    search = (search or '').strip().upper()
    cancel_join, cancel_description, cancel_search = _resolve_cancel_join(
        session
    )
    optional_columns = _resolve_solsai_optional_columns(session)

    where_status = ''
    if not include_atendidas:
        where_status = """
          and not (
            nvl(it.qtd_atendida, 0) >= nvl(it.qtd_solicitada, 0)
            and nvl(it.qtd_solicitada, 0) > 0
          )
          and nvl(sp.tp_situacao, ' ') <> 'A'
          and sp.dt_cancelamento is null
          and sp.cd_motivo_canc is null
        """

    sql = f"""
        select *
          from (
            with itens_resumo as (
              select
                  isp.cd_solsai_pro,
                  sum(nvl(isp.qt_solicitado, 0)) as qtd_solicitada,
                  sum(nvl(isp.qt_atendida, 0)) as qtd_atendida
                from dbamv.itsolsai_pro isp
               group by isp.cd_solsai_pro
            ),
            ult_mov as (
              select cd_atendimento, cd_leito
                from (
                  select
                      mi.cd_atendimento,
                      mi.cd_leito,
                      row_number() over (
                        partition by mi.cd_atendimento
                        order by nvl(mi.hr_mov_int, mi.dt_mov_int) desc,
                                 mi.cd_mov_int desc
                      ) as rn
                    from dbamv.mov_int mi
                   where mi.cd_leito is not null
                )
               where rn = 1
            )
            select
                sp.cd_solsai_pro as solicitacao,
                sp.cd_atendimento as atendimento,
                case
                  when trim(st.nm_setor) is not null then st.nm_setor
                  when trim(st_est.nm_setor) is not null then st_est.nm_setor
                  when trim(estsol.ds_estoque) is not null then estsol.ds_estoque
                  else null
                end as setor,
                case
                  when trim(st.nm_setor) is not null then st.cd_cen_cus
                  else st_est.cd_cen_cus
                end as centro_custo_solicitante,
                case
                  when trim(st.nm_setor) is null
                   and st_est.cd_cen_cus is not null then 'S'
                  else 'N'
                end as setor_por_centro_custo,
                nvl(lei.ds_leito, to_char(coalesce(atd.cd_leito, um.cd_leito)))
                  as leito,
                ui.ds_unid_int as unidade_internacao,
                sti.nm_setor as setor_internacao,
                pac.nm_paciente as paciente,
                sp.cd_pre_med as prescricao,
                cast(nvl(sp.hr_solsai_pro, sp.dt_solsai_pro) as date)
                  as data_solicitacao,
                sp.dt_cancelamento as data_cancelamento,
                sp.cd_motivo_canc as codigo_motivo_cancelamento,
                {cancel_description},
                {optional_columns['justificativa']} as justificativa_cancelamento,
                case
                  when nvl(sp.tp_situacao, ' ') = 'A'
                    or sp.dt_cancelamento is not null
                    or sp.cd_motivo_canc is not null then 'Cancelada'
                  when nvl(it.qtd_atendida, 0) >= nvl(it.qtd_solicitada, 0)
                   and nvl(it.qtd_solicitada, 0) > 0 then 'Atendida'
                  when nvl(it.qtd_atendida, 0) > 0 then 'Parcial'
                  else 'Pendente'
                end as status,
                nvl(sp.sn_urgente, 'N') as urgente,
                'N' as alergia
              from dbamv.solsai_pro sp
              left join itens_resumo it on it.cd_solsai_pro = sp.cd_solsai_pro
              left join dbamv.atendime atd
                on atd.cd_atendimento = sp.cd_atendimento
              left join dbamv.paciente pac on pac.cd_paciente = atd.cd_paciente
              left join dbamv.setor st on st.cd_setor = sp.cd_setor
              left join dbamv.estoque estsol
                on estsol.cd_estoque = sp.cd_estoque_solicitante
              left join dbamv.setor st_est on st_est.cd_setor = estsol.cd_setor
              left join ult_mov um on um.cd_atendimento = sp.cd_atendimento
              left join dbamv.leito lei
                on lei.cd_leito = coalesce(atd.cd_leito, um.cd_leito)
              left join dbamv.unid_int ui on ui.cd_unid_int = lei.cd_unid_int
              left join dbamv.setor sti on sti.cd_setor = ui.cd_setor
              {cancel_join}
             where cast(nvl(sp.hr_solsai_pro, sp.dt_solsai_pro) as date)
                   >= to_date(:date_from, 'YYYY-MM-DD')
               and cast(nvl(sp.hr_solsai_pro, sp.dt_solsai_pro) as date)
                   < to_date(:date_to, 'YYYY-MM-DD') + 1
               and sp.cd_estoque = :estoque_id
               {where_status}
               and (
                    :search is null
                 or :search = ''
                 or upper(st.nm_setor) like '%' || :search || '%'
                 or upper(st_est.nm_setor) like '%' || :search || '%'
                 or upper(estsol.ds_estoque) like '%' || :search || '%'
                 or upper(lei.ds_leito) like '%' || :search || '%'
                 or upper(ui.ds_unid_int) like '%' || :search || '%'
                 or upper(pac.nm_paciente) like '%' || :search || '%'
                 or upper({cancel_search}) like '%' || :search || '%'
                 or to_char(sp.cd_atendimento) like '%' || :search || '%'
                 or to_char(sp.cd_solsai_pro) like '%' || :search || '%'
               )
             order by
               case
                 when nvl(sp.tp_situacao, ' ') = 'A'
                   or sp.dt_cancelamento is not null
                   or sp.cd_motivo_canc is not null then 3
                 when nvl(it.qtd_atendida, 0) >= nvl(it.qtd_solicitada, 0)
                  and nvl(it.qtd_solicitada, 0) > 0 then 2
                 when nvl(sp.sn_urgente, 'N') = 'S' then 0
                 else 1
               end,
               cast(nvl(sp.hr_solsai_pro, sp.dt_solsai_pro) as date)
          )
         where rownum <= :max_rows
    """
    rows = (
        session.execute(
            text(sql),
            {
                'date_from': date_from.isoformat(),
                'date_to': date_to.isoformat(),
                'max_rows': max_rows,
                'search': search,
                'estoque_id': FARMACIA_ESTOQUE_ID,
            },
        )
        .mappings()
        .all()
    )
    normalized_rows = [_normalize_row(row) for row in rows]
    items_by_solicitacao = _query_items(
        session,
        [row['solicitacao'] for row in normalized_rows],
    )
    for row in normalized_rows:
        row['items'] = items_by_solicitacao.get(str(row['solicitacao']), [])
        row['allergy'] = any(item.get('allergy') for item in row['items'])

    return normalized_rows


@router.get('/solicitacoes', status_code=HTTPStatus.OK)
def listar_solicitacoes_farmacia(
    max_rows: int = Query(default=250, ge=1, le=1000),
    lookback_hours: int | None = Query(default=None, ge=1, le=168),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    include_atendidas: bool = Query(default=True),
    search: str = Query(default=''),
    session: Session = Depends(get_session_oracle),
):
    global _last_rows, _last_success_at
    try:
        if date_from is None:
            if lookback_hours:
                date_from = date.today() - timedelta(hours=lookback_hours)
            else:
                date_from = date.today()
        if date_to is None:
            date_to = date_from

        rows = _query_soulmv(
            session=session,
            max_rows=max_rows,
            include_atendidas=include_atendidas,
            date_from=date_from,
            date_to=date_to,
            search=search,
        )
    except SQLAlchemyError as exc:
        with _cache_lock:
            cached_rows = list(_last_rows)
            cached_at = _last_success_at

        if cached_rows:
            return {
                'source': 'cache',
                'updatedAt': cached_at.isoformat() if cached_at else _now_iso(),
                'warning': (
                    'Nao foi possivel consultar o SoulMV. '
                    'Exibindo ultimo retorno valido.'
                ),
                'rows': cached_rows,
            }

        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel consultar as solicitacoes da farmacia.',
        ) from exc

    with _cache_lock:
        _last_rows = rows
        _last_success_at = datetime.now()

    return {'source': 'soulmv', 'updatedAt': _now_iso(), 'rows': rows}


@router.get('/health', status_code=HTTPStatus.OK)
def farmacia_health(session: Session = Depends(get_session_oracle)):
    try:
        session.execute(text('select 1 from dual')).scalar()
    except SQLAlchemyError as exc:
        return {'ok': False, 'error': str(exc), 'checkedAt': _now_iso()}

    return {'ok': True, 'source': 'soulmv', 'checkedAt': _now_iso()}
