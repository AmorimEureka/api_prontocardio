from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from http import HTTPStatus
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Numeric, String, and_, case, cast, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app_prontocardio.database import get_session_oracle, get_session_postgres
from app_prontocardio.models import (
    AuditoriaConciliacaoFaturamento,
    ConciliacaoFaturamento,
    ConciliacaoFaturamentoRemessa,
    LancamentoExtratoBancario,
    ModelContaAtendimento,
    ModelGruPro,
    ModelHpcContaBancaria,
    ModelHpcConvenio,
    ModelProFat,
    NfseXml,
    ProcessoConciliacaoRemessa,
    RecebimentoRemessa,
    RegistroGlosa,
    RemessaFinanceira,
    TipoAtendimento,
    Usuario,
)
from app_prontocardio.schema import (
    ConciliacaoAlteracaoPublic,
    ConciliacaoFaturamentoCreate,
    ConciliacaoFaturamentoPublic,
    ConciliacaoFaturamentoUpdate,
    ConciliacaoRemessaCreate,
    ConciliacaoRemessaPublic,
    ConciliacoesGerenciamentoList,
    ConciliacoesSemRecebimentoList,
    ContasBancariasRecebimentoList,
    FollowUpGlosasList,
    LancamentosExtratoBancarioList,
    NfseConciliacaoRemessaInput,
    NfsesPendentesConciliacao,
    NfsesSaldoRemessaList,
    RecebimentoRemessaCreate,
    RecebimentoRemessaPublic,
    RecebimentoRemessaUpdate,
    RecebimentosRemessaList,
    RemessasConciliacaoList,
    RemessasFaturamentoList,
)
from app_prontocardio.security import valida_token_usuario_atual
from app_prontocardio.services.remessas import (
    sincronizar_totais_remessas_financeiras,
)

router = APIRouter(
    prefix='/app_glosas/financeiro',
    tags=['financeiro'],
)

ValidaUsuarioAtual = Annotated[Usuario, Depends(valida_token_usuario_atual)]
SessionPostgres = Annotated[Session, Depends(get_session_postgres)]
CENTAVOS = Decimal('0.01')
ORACLE_IN_CHUNK_SIZE = 900
MENSAGEM_VALORES_DIVERGENTES = (
    'O valor total das remessas descontadas do total de glosas é diferente '
    'do valor total da nota fiscal. Informe valor de glosa ou valide se as '
    'remessas realmente pertencem à nota fiscal.'
)


def _money(value) -> Decimal:
    if value in (None, ''):
        return Decimal('0.00')

    raw_value = str(value).strip().replace('R$', '').replace(' ', '')
    if ',' in raw_value:
        raw_value = raw_value.replace('.', '').replace(',', '.')
    try:
        return Decimal(raw_value).quantize(CENTAVOS, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return Decimal('0.00')


def _normalize_cnpj(value) -> str:
    return ''.join(
        character for character in str(value or '') if character.isdigit()
    )


def _is_oracle_connect_timeout(exc: SQLAlchemyError) -> bool:
    errors = [
        str(exc),
        str(getattr(exc, 'orig', '')),
        str(exc.__cause__ or ''),
    ]
    return any(
        code in error
        for error in errors
        for code in ('ORA-12170', 'ORA-12545')
    )


def _nota_publica(nota: NfseXml, convenio: dict | None = None) -> dict:
    impostos = sum(
        (
            _money(value)
            for value in (
                nota.valor_pis,
                nota.valor_cofins,
                nota.valor_csll,
                nota.valor_ir,
                nota.valor_inss,
                nota.outras_retencoes,
                nota.valor_iss_retido,
            )
        ),
        Decimal('0.00'),
    )
    return {
        'row_hash': nota.row_hash,
        'numero_nfse': nota.numero_nfse or '-',
        'data_emissao': nota.data_hora,
        'convenio': (
            convenio['convenio']
            if convenio is not None
            else (
                str(nota.tomador_razao_social).strip()
                if nota.tomador_razao_social
                and str(nota.tomador_razao_social).strip()
                else 'Convenio nao informado'
            )
        ),
        'cnpj_convenio': (
            convenio['cnpj_convenio']
            if convenio is not None
            else _normalize_cnpj(nota.prestador_cnpj or nota.tomador_cnpj)
        ),
        'impostos': impostos.quantize(CENTAVOS),
        'valor_nfse': _money(nota.valor_liquido_nfse),
    }


def _consultar_convenios_hpc(session_oracle: Session) -> dict[str, dict]:
    rows = session_oracle.execute(
        select(
            ModelHpcConvenio.cd_convenio,
            ModelHpcConvenio.cnpj_convenio,
            ModelHpcConvenio.nm_convenio,
        ).order_by(ModelHpcConvenio.nm_convenio)
    ).all()
    convenios = {}
    for row in rows:
        cnpj = _normalize_cnpj(row.cnpj_convenio)
        if cnpj and cnpj not in convenios:
            convenios[cnpj] = {
                'cd_convenio': int(row.cd_convenio),
                'cnpj_convenio': cnpj,
                'convenio': row.nm_convenio,
            }
    return convenios


def _convenio_da_nfse(
    nota: NfseXml,
    convenios_por_cnpj: dict[str, dict],
) -> dict | None:
    # Mantem a chave solicitada e cobre os XMLs atuais, nos quais o convenio
    # esta identificado como tomador do servico.
    return convenios_por_cnpj.get(
        _normalize_cnpj(nota.prestador_cnpj)
    ) or convenios_por_cnpj.get(_normalize_cnpj(nota.tomador_cnpj))


def _consultar_remessas_hpc(  # noqa: PLR0913
    session_oracle: Session,
    cnpj_convenio: str,
    cd_remessas_usadas: set[int],
    cd_remessas: set[int] | None = None,
    q: str | None = None,
    limit: int = 50,
) -> list[dict]:
    cnpj_normalizado = _normalize_cnpj(cnpj_convenio)
    contas_distintas = (
        select(
            ModelContaAtendimento.cd_remessa.label('cd_remessa'),
            ModelContaAtendimento.cd_convenio.label('cd_convenio'),
            ModelContaAtendimento.cnpj_convenio.label('cnpj_convenio'),
            ModelContaAtendimento.nm_convenio.label('convenio'),
            ModelContaAtendimento.cd_reg.label('cd_reg'),
            ModelContaAtendimento.vl_total_registro.label(
                'valor_registro'
            ),
        )
        .where(ModelContaAtendimento.cd_remessa.is_not(None))
        .where(
            func.regexp_replace(
                ModelContaAtendimento.cnpj_convenio,
                '[^0-9]',
                '',
            )
            == cnpj_normalizado
        )
        .distinct()
    )
    if cd_remessas_usadas:
        remessas_ordenadas = sorted(cd_remessas_usadas)
        for offset in range(
            0,
            len(remessas_ordenadas),
            ORACLE_IN_CHUNK_SIZE,
        ):
            chunk = remessas_ordenadas[offset : offset + ORACLE_IN_CHUNK_SIZE]
            contas_distintas = contas_distintas.where(
                ModelContaAtendimento.cd_remessa.not_in(chunk)
            )
    if cd_remessas is not None:
        contas_distintas = contas_distintas.where(
            ModelContaAtendimento.cd_remessa.in_(cd_remessas)
        )
    if q:
        termo_pesquisa = q.strip()
        if termo_pesquisa.isdigit():
            contas_distintas = contas_distintas.where(
                ModelContaAtendimento.cd_remessa == int(termo_pesquisa)
            )
        else:
            termo = f'%{termo_pesquisa}%'
            contas_distintas = contas_distintas.where(
                or_(
                    cast(ModelContaAtendimento.cd_remessa, String(50)).ilike(
                        termo
                    ),
                    ModelContaAtendimento.nm_convenio.ilike(termo),
                )
            )

    contas_distintas = contas_distintas.subquery()
    query = (
        select(
            contas_distintas.c.cd_remessa,
            contas_distintas.c.cd_convenio,
            contas_distintas.c.cnpj_convenio,
            contas_distintas.c.convenio,
            func.sum(
                func.coalesce(contas_distintas.c.valor_registro, 0)
            ).label('valor_total'),
        )
        .group_by(
            contas_distintas.c.cd_remessa,
            contas_distintas.c.cd_convenio,
            contas_distintas.c.cnpj_convenio,
            contas_distintas.c.convenio,
        )
        .order_by(contas_distintas.c.cd_remessa.desc())
        .limit(limit)
    )

    return [
        {
            'cd_remessa': int(row.cd_remessa),
            'cd_convenio': (
                int(row.cd_convenio) if row.cd_convenio is not None else None
            ),
            'convenio': row.convenio or 'Convenio nao informado',
            'cnpj_convenio': _normalize_cnpj(row.cnpj_convenio),
            'valor_total': _money(row.valor_total),
        }
        for row in session_oracle.execute(query).all()
    ]


def _consultar_cards_remessas_hpc(  # noqa: PLR0913
    session_oracle: Session,
    cd_remessas_encerradas: set[int],
    q: str | None = None,
    numero_remessa: str | None = None,
    convenio: str | None = None,
    cd_remessas_nfse: set[int] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict], int]:
    if cd_remessas_nfse is not None and not cd_remessas_nfse:
        return [], 0
    termo_remessa = (numero_remessa or '').strip()
    if termo_remessa and not termo_remessa.isdigit():
        return [], 0
    cnpj_convenio = func.coalesce(
        ModelHpcConvenio.cnpj_convenio,
        ModelContaAtendimento.cnpj_convenio,
    )
    nome_convenio = func.coalesce(
        ModelHpcConvenio.nm_convenio,
        ModelContaAtendimento.nm_convenio,
    )
    contas_distintas = (
        select(
            ModelContaAtendimento.cd_remessa.label('cd_remessa'),
            ModelContaAtendimento.cd_convenio.label('cd_convenio'),
            cnpj_convenio.label('cnpj_convenio'),
            nome_convenio.label('convenio'),
            ModelContaAtendimento.cd_reg.label('cd_reg'),
            ModelContaAtendimento.vl_total_registro.label(
                'valor_registro'
            ),
            ModelContaAtendimento.dt_competencia.label('data_competencia'),
        )
        .select_from(ModelContaAtendimento)
        .outerjoin(
            ModelHpcConvenio,
            ModelHpcConvenio.cd_convenio
            == ModelContaAtendimento.cd_convenio,
        )
        .where(ModelContaAtendimento.cd_remessa.is_not(None))
        .distinct()
    )
    if cd_remessas_encerradas:
        remessas_ordenadas = sorted(cd_remessas_encerradas)
        for chunk_offset in range(
            0,
            len(remessas_ordenadas),
            ORACLE_IN_CHUNK_SIZE,
        ):
            chunk = remessas_ordenadas[
                chunk_offset : chunk_offset + ORACLE_IN_CHUNK_SIZE
            ]
            contas_distintas = contas_distintas.where(
                ModelContaAtendimento.cd_remessa.not_in(chunk)
            )
    if termo_remessa:
        contas_distintas = contas_distintas.where(
            ModelContaAtendimento.cd_remessa == int(termo_remessa)
        )
    termo_convenio = (convenio or '').strip()
    if termo_convenio:
        pattern_convenio = f'%{termo_convenio}%'
        contas_distintas = contas_distintas.where(
            or_(
                nome_convenio.ilike(pattern_convenio),
                cnpj_convenio.ilike(pattern_convenio),
            )
        )
    if cd_remessas_nfse is not None:
        contas_distintas = contas_distintas.where(
            ModelContaAtendimento.cd_remessa.in_(cd_remessas_nfse)
        )
    termo_pesquisa = (q or '').strip()
    if termo_pesquisa:
        if termo_pesquisa.isdigit():
            contas_distintas = contas_distintas.where(
                ModelContaAtendimento.cd_remessa == int(termo_pesquisa)
            )
        else:
            pattern = f'%{termo_pesquisa}%'
            contas_distintas = contas_distintas.where(
                or_(
                    nome_convenio.ilike(pattern),
                    cnpj_convenio.ilike(pattern),
                )
            )

    contas_distintas = contas_distintas.subquery()
    agrupadas = (
        select(
            contas_distintas.c.cd_remessa,
            contas_distintas.c.cd_convenio,
            contas_distintas.c.cnpj_convenio,
            contas_distintas.c.convenio,
            func.max(contas_distintas.c.data_competencia).label(
                'data_competencia'
            ),
            func.sum(
                func.coalesce(contas_distintas.c.valor_registro, 0)
            ).label('valor_total'),
        )
        .group_by(
            contas_distintas.c.cd_remessa,
            contas_distintas.c.cd_convenio,
            contas_distintas.c.cnpj_convenio,
            contas_distintas.c.convenio,
        )
        .subquery()
    )
    rows = session_oracle.execute(
        select(
            agrupadas,
            func.count().over().label('total_registros'),
        )
        .order_by(agrupadas.c.cd_remessa.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    total = int(rows[0].total_registros) if rows else 0
    if not rows and offset:
        total = int(
            session_oracle.scalar(
                select(func.count()).select_from(agrupadas)
            )
            or 0
        )
    return (
        [
            {
                'cd_remessa': int(row.cd_remessa),
                'cd_convenio': (
                    int(row.cd_convenio)
                    if row.cd_convenio is not None
                    else None
                ),
                'convenio': row.convenio or 'Convenio nao informado',
                'cnpj_convenio': _normalize_cnpj(row.cnpj_convenio),
                'data_competencia': row.data_competencia,
                'valor_total': _money(row.valor_total),
            }
            for row in rows
        ],
        total,
    )


def _codigos_remessas_por_nfse(
    session: Session,
    numero_nfse: str | None,
) -> set[int] | None:
    termo = (numero_nfse or '').strip()
    if not termo:
        return None
    return set(
        session.scalars(
            select(ConciliacaoFaturamentoRemessa.cd_remessa)
            .join(
                ConciliacaoFaturamento,
                ConciliacaoFaturamento.id
                == ConciliacaoFaturamentoRemessa.conciliacao_id,
            )
            .where(
                ConciliacaoFaturamento.ativo.is_(True),
                ConciliacaoFaturamento.numero_nfse.ilike(f'%{termo}%'),
            )
            .distinct()
        )
    )


def _valor_alocado_vinculo(
    vinculo: ConciliacaoFaturamentoRemessa,
) -> Decimal:
    valor_alocado = _money(vinculo.valor_alocado_nfse)
    if valor_alocado > 0:
        return valor_alocado
    return max(
        _money(vinculo.valor_total) - _money(vinculo.valor_glosado),
        Decimal('0.00'),
    )


def _valor_impostos_vinculo(
    vinculo: ConciliacaoFaturamentoRemessa,
) -> Decimal:
    return _money(vinculo.valor_impostos)


def _valor_conciliado_vinculo(
    vinculo: ConciliacaoFaturamentoRemessa,
) -> Decimal:
    return _valor_alocado_vinculo(vinculo) + _valor_impostos_vinculo(
        vinculo
    )


def _usuario_operacao_publico(usuario: Usuario | None) -> dict | None:
    if usuario is None:
        return None
    return {
        'id': usuario.id,
        'nome': usuario.nome,
        'email': usuario.email,
    }


def _snapshot_conciliacao(
    conciliacao: ConciliacaoFaturamento,
    vinculos: list[ConciliacaoFaturamentoRemessa] | None = None,
    recebimento: RecebimentoRemessa | None = None,
) -> dict:
    snapshot = {
        'numero_nfse': conciliacao.numero_nfse,
        'processo_recebimento': conciliacao.processo_recebimento,
        'data_previsao_recebimento': str(
            conciliacao.data_previsao_recebimento
        ),
        'data_recebimento': (
            str(conciliacao.data_recebimento)
            if conciliacao.data_recebimento
            else None
        ),
        'ativo': conciliacao.ativo,
    }
    if vinculos is not None:
        snapshot['remessas'] = [
            {
                'cd_remessa': vinculo.cd_remessa,
                'valor_alocado_nfse': str(
                    _valor_alocado_vinculo(vinculo)
                ),
                'valor_impostos': str(_valor_impostos_vinculo(vinculo)),
                'valor_glosado': str(_money(vinculo.valor_glosado)),
                'tipo_conciliacao': vinculo.tp_conciliacao,
            }
            for vinculo in vinculos
        ]
    if recebimento is not None:
        snapshot['recebimento'] = {
            'id': recebimento.id,
            'cd_remessa': recebimento.cd_remessa,
            'data_recebimento': str(recebimento.data_recebimento),
            'valor_recebido': str(_money(recebimento.valor_recebido)),
            'conta_bancaria_id': recebimento.conta_bancaria_id,
            'conta_plano_contas': recebimento.conta_plano_contas,
            'conta_centro_custo': recebimento.conta_centro_custo,
            'lancamento_extrato_id': recebimento.lancamento_extrato_id,
        }
    return snapshot


def _registrar_auditoria_conciliacao(  # noqa: PLR0913
    session: Session,
    conciliacao_id: int,
    acao: str,
    usuario_id: int,
    dados_anteriores: dict | None = None,
    dados_novos: dict | None = None,
) -> AuditoriaConciliacaoFaturamento:
    auditoria = AuditoriaConciliacaoFaturamento(
        conciliacao_id=conciliacao_id,
        acao=acao,
        usuario_id=usuario_id,
        dados_anteriores=dados_anteriores,
        dados_novos=dados_novos,
    )
    auditoria.data_operacao = datetime.now(
        ZoneInfo('America/Sao_Paulo')
    ).replace(tzinfo=None)
    session.add(auditoria)
    return auditoria


def _consultar_itens_remessas_hpc(
    session_oracle: Session,
    cnpj_convenio: str,
    cd_remessas: set[int],
) -> list[dict]:
    if not cd_remessas:
        return []

    cnpj_normalizado = _normalize_cnpj(cnpj_convenio)
    query = (
        select(
            ModelContaAtendimento,
            ModelGruPro.cd_gru_pro,
            ModelGruPro.ds_gru_pro,
        )
        .select_from(ModelContaAtendimento)
        .outerjoin(
            ModelProFat,
            ModelProFat.cd_pro_fat == ModelContaAtendimento.cd_pro_fat,
        )
        .outerjoin(
            ModelGruPro,
            ModelGruPro.cd_gru_pro == ModelProFat.cd_gru_pro,
        )
        .where(
            ModelContaAtendimento.cd_remessa.in_(cd_remessas),
            func.regexp_replace(
                ModelContaAtendimento.cnpj_convenio,
                '[^0-9]',
                '',
            )
            == cnpj_normalizado,
        )
        .order_by(
            ModelContaAtendimento.cd_remessa,
            ModelContaAtendimento.cd_atendimento,
            ModelContaAtendimento.cd_reg,
            ModelContaAtendimento.cd_lancamento,
        )
    )
    rows = session_oracle.execute(query).all()
    itens = []
    chaves_adicionadas = set()
    for row, cd_gru_pro, ds_gru_pro in rows:
        chave = (int(row.cd_remessa), int(row.cd_reg), int(row.cd_lancamento))
        if chave in chaves_adicionadas:
            continue
        chaves_adicionadas.add(chave)
        itens.append(
            {
                'codigo_paciente': int(row.cd_paciente or 0),
                'nm_paciente': row.nm_paciente,
                'cd_remessa': int(row.cd_remessa),
                'cd_atendimento': int(row.cd_atendimento or 0),
                'conta': int(row.cd_reg),
                'cd_lancamento': int(row.cd_lancamento),
                'cd_prestador': int(row.cd_prestador or 0),
                'cd_convenio': int(row.cd_convenio or 0),
                'tp_atendimento': (
                    row.tp_atendimento or TipoAtendimento.EXTERNO.value
                ),
                'procedimento': str(row.cd_pro_fat or '-'),
                'cd_gru_pro': int(cd_gru_pro or 0),
                'ds_gru_pro': ds_gru_pro or 'Grupo nao informado',
                'cd_gru_fat': int(row.cd_gru_fat or 0),
                'ds_gru_fat': row.ds_gru_fat or 'Grupo nao informado',
                'convenio': row.nm_convenio or 'Convenio nao informado',
                'guia': str(row.nr_guia or '-'),
                'prestador': row.nm_prestador or 'Prestador nao informado',
                'data_atendimento': (
                    row.dt_atendimento
                    or row.dt_lancamento
                    or datetime.now(ZoneInfo('America/Sao_Paulo')).replace(
                        tzinfo=None
                    )
                ),
                'valor': _money(row.vl_total_conta),
                'qtd_registro': max(
                    _money(row.qt_lancamento),
                    Decimal('1.00'),
                ),
                'descricao_item': row.descricao,
                'data_alta': row.dt_alta,
                'data_lancamento': row.dt_lancamento,
            }
        )
    return itens


def _remessas_conciliadas(session: Session) -> set[int]:
    ultimos_ids = (
        select(func.max(ConciliacaoFaturamentoRemessa.id).label('id'))
        .join(
            ConciliacaoFaturamento,
            ConciliacaoFaturamento.id
            == ConciliacaoFaturamentoRemessa.conciliacao_id,
        )
        .where(ConciliacaoFaturamento.ativo.is_(True))
        .group_by(ConciliacaoFaturamentoRemessa.cd_remessa)
        .subquery()
    )
    remessas_modeladas = set(
        session.scalars(
            select(RemessaFinanceira.cd_remessa).where(
                RemessaFinanceira.recebimento_integral.is_(True)
            )
        )
    )
    remessas_legadas = set(
        session.scalars(
            select(ConciliacaoFaturamentoRemessa.cd_remessa)
            .where(
                ConciliacaoFaturamentoRemessa.id.in_(select(ultimos_ids.c.id)),
                ~select(RemessaFinanceira.cd_remessa)
                .where(
                    RemessaFinanceira.cd_remessa
                    == ConciliacaoFaturamentoRemessa.cd_remessa
                )
                .exists(),
                or_(
                    ConciliacaoFaturamentoRemessa.sn_glosado != 'true',
                    ConciliacaoFaturamentoRemessa.valor_glosado <= 0,
                )
            )
            .distinct()
        )
    )
    return remessas_modeladas | remessas_legadas


def _remessas_previamente_conciliadas(
    session: Session,
    cd_remessas: set[int] | None = None,
) -> set[int]:
    query = (
        select(ConciliacaoFaturamentoRemessa.cd_remessa)
        .join(
            ConciliacaoFaturamento,
            ConciliacaoFaturamento.id
            == ConciliacaoFaturamentoRemessa.conciliacao_id,
        )
        .where(ConciliacaoFaturamento.ativo.is_(True))
        .distinct()
    )
    if cd_remessas is not None:
        if not cd_remessas:
            return set()
        query = query.where(
            ConciliacaoFaturamentoRemessa.cd_remessa.in_(cd_remessas)
        )
    return set(session.scalars(query))


def _valores_acatados_por_remessa(
    session: Session,
    cd_remessas: set[int] | None = None,
) -> dict[int, Decimal]:
    valor_acatado = func.sum(func.coalesce(RegistroGlosa.valor_recursado, 0))
    query = (
        select(
            RegistroGlosa.cd_remessa,
            valor_acatado.label('valor_acatado'),
        )
        .where(
            RegistroGlosa.sn_ativo == 'true',
            RegistroGlosa.sn_glosado == 'not',
            RegistroGlosa.processo_recurso.is_not(None),
            func.trim(RegistroGlosa.processo_recurso) != '',
            RegistroGlosa.dt_recurso.is_not(None),
        )
        .group_by(RegistroGlosa.cd_remessa)
        .having(valor_acatado > 0)
    )
    if cd_remessas is not None:
        if not cd_remessas:
            return {}
        query = query.where(RegistroGlosa.cd_remessa.in_(cd_remessas))
    return {
        int(row.cd_remessa): _money(row.valor_acatado)
        for row in session.execute(query).all()
    }


def _saldos_recebimento_por_remessa(
    session: Session,
    cd_remessas: set[int] | None = None,
    valores_acatados: dict[int, Decimal] | None = None,
) -> dict[int, Decimal]:
    if valores_acatados is None:
        valores_acatados = _valores_acatados_por_remessa(
            session,
            cd_remessas,
        )
    valor_recebido = func.coalesce(
        func.sum(RecebimentoRemessa.valor_recebido),
        0,
    )
    query = (
        select(
            RemessaFinanceira.cd_remessa,
            (RemessaFinanceira.valor_total - valor_recebido).label('saldo'),
        )
        .outerjoin(
            RecebimentoRemessa,
            RecebimentoRemessa.cd_remessa == RemessaFinanceira.cd_remessa,
        )
        .where(RemessaFinanceira.recebimento_integral.is_(False))
        .group_by(
            RemessaFinanceira.cd_remessa,
            RemessaFinanceira.valor_total,
        )
        .having(RemessaFinanceira.valor_total - valor_recebido > 0)
    )
    if cd_remessas is not None:
        if not cd_remessas:
            return {}
        query = query.where(RemessaFinanceira.cd_remessa.in_(cd_remessas))
    saldos = {}
    for row in session.execute(query).all():
        cd_remessa = int(row.cd_remessa)
        saldo = _money(row.saldo) - valores_acatados.get(
            cd_remessa,
            Decimal('0.00'),
        )
        if saldo > 0:
            saldos[cd_remessa] = _money(saldo)
    return saldos


def _remessas_encerradas_por_acato(
    session: Session,
    valores_acatados: dict[int, Decimal],
    saldos_recebimento: dict[int, Decimal],
) -> set[int]:
    if not valores_acatados:
        return set()
    remessas_com_controle = set(
        session.scalars(
            select(RemessaFinanceira.cd_remessa).where(
                RemessaFinanceira.cd_remessa.in_(valores_acatados),
                RemessaFinanceira.recebimento_integral.is_(False),
            )
        )
    )
    return remessas_com_controle - saldos_recebimento.keys()


def _valor_reais_mensagem(valor: Decimal) -> str:
    return f'R$ {_money(valor):.2f}'.replace('.', ',')


def _restricoes_nova_conciliacao(
    remessas_previamente_conciliadas: set[int],
    remessas_recebidas_integralmente: set[int],
    remessas_encerradas_por_acato: set[int],
    recursos_abertos: dict[int, Decimal],
    valores_acatados: dict[int, Decimal],
) -> dict[int, str]:
    remessas_sem_recurso = (
        remessas_previamente_conciliadas - recursos_abertos.keys()
    )
    restricoes = {
        cd_remessa: (
            f'A remessa {cd_remessa} foi integralmente recebida e '
            'conciliada.'
        )
        for cd_remessa in (
            remessas_recebidas_integralmente & remessas_sem_recurso
        )
    }
    restricoes.update(
        {
            cd_remessa: (
                f'A remessa {cd_remessa} foi encerrada financeiramente: o '
                'saldo remanescente foi integralmente acatado.'
            )
            for cd_remessa in (
                remessas_encerradas_por_acato & remessas_sem_recurso
            )
        }
    )
    remessas_conciliadas_sem_recurso = (
        remessas_sem_recurso
        - remessas_recebidas_integralmente
        - remessas_encerradas_por_acato
    )
    for cd_remessa in remessas_conciliadas_sem_recurso:
        restricoes[cd_remessa] = (
            f'A remessa {cd_remessa} já possui conciliação anterior e não '
            'possui recurso disponível para uma nova conciliação.'
        )
    acatos_historicos_sem_recurso = (
        valores_acatados.keys()
        - recursos_abertos.keys()
        - remessas_previamente_conciliadas
        - remessas_encerradas_por_acato
    )
    for cd_remessa in acatos_historicos_sem_recurso:
        restricoes[cd_remessa] = (
            f'A remessa {cd_remessa} possui apenas valor acatado. Acatos são '
            'perdas reconhecidas e não podem gerar uma nova conciliação.'
        )
    return restricoes


def _restricao_remessa_publica(  # noqa: PLR0913
    cd_remessa: int,
    message: str,
    remessas_previamente_conciliadas: set[int],
    remessas_recebidas_integralmente: set[int],
    remessas_encerradas_por_acato: set[int],
    saldos_recebimento: dict[int, Decimal],
    valores_acatados: dict[int, Decimal],
) -> dict:
    recebida_integralmente = cd_remessa in remessas_recebidas_integralmente
    encerrada_por_acato = cd_remessa in remessas_encerradas_por_acato
    if encerrada_por_acato:
        motivo = 'encerrada_por_acato'
    elif recebida_integralmente:
        motivo = 'recebida_integralmente'
    elif cd_remessa in remessas_previamente_conciliadas:
        motivo = 'conciliacao_sem_recurso'
    elif cd_remessa in valores_acatados:
        motivo = 'acato_sem_recurso'
    else:
        motivo = 'indisponivel'
    return {
        'cd_remessa': cd_remessa,
        'motivo': motivo,
        'message': message,
        'valor_total_acatado': valores_acatados.get(
            cd_remessa,
            Decimal('0.00'),
        ),
        'saldo_cobravel': (
            Decimal('0.00')
            if recebida_integralmente or encerrada_por_acato
            else saldos_recebimento.get(cd_remessa)
        ),
        'remessa_recebida_integralmente': recebida_integralmente,
        'remessa_encerrada_financeiramente': (
            recebida_integralmente or encerrada_por_acato
        ),
    }


def _remessas_conciliadas_com_glosa(session: Session) -> set[int]:
    ultimos_ids = (
        select(func.max(ConciliacaoFaturamentoRemessa.id).label('id'))
        .join(
            ConciliacaoFaturamento,
            ConciliacaoFaturamento.id
            == ConciliacaoFaturamentoRemessa.conciliacao_id,
        )
        .where(ConciliacaoFaturamento.ativo.is_(True))
        .group_by(ConciliacaoFaturamentoRemessa.cd_remessa)
        .subquery()
    )
    return set(
        session.scalars(
            select(ConciliacaoFaturamentoRemessa.cd_remessa)
            .where(
                ConciliacaoFaturamentoRemessa.id.in_(select(ultimos_ids.c.id)),
                ConciliacaoFaturamentoRemessa.sn_glosado == 'true',
                ConciliacaoFaturamentoRemessa.valor_glosado > 0,
            )
            .distinct()
        )
    )


def _recursos_abertos_por_remessa(
    session: Session,
    cd_remessas: set[int] | None = None,
) -> dict[int, Decimal]:
    valor_registro = func.coalesce(RegistroGlosa.valor_recursado, 0)
    valor_recebido = func.coalesce(RegistroGlosa.valor_recebido, 0)
    valor_sem_pagamento = case(
        (
            valor_recebido == 0,
            valor_registro,
        ),
        else_=0,
    )
    valor_total = func.sum(valor_registro)
    valor_aberto = func.sum(valor_sem_pagamento)
    query = (
        select(
            RegistroGlosa.cd_remessa,
            valor_total.label('valor_recursado_total'),
            valor_aberto.label('valor_recursado_aberto'),
        )
        .where(
            RegistroGlosa.sn_ativo == 'true',
            RegistroGlosa.sn_glosado == 'true',
            RegistroGlosa.processo_recurso.is_not(None),
            func.trim(RegistroGlosa.processo_recurso) != '',
            RegistroGlosa.dt_recurso.is_not(None),
        )
        .group_by(RegistroGlosa.cd_remessa)
        .having(valor_total > 0)
    )
    if cd_remessas is not None:
        if not cd_remessas:
            return {}
        query = query.where(RegistroGlosa.cd_remessa.in_(cd_remessas))

    totais = {
        int(row.cd_remessa): (
            _money(row.valor_recursado_total),
            _money(row.valor_recursado_aberto),
        )
        for row in session.execute(query).all()
    }
    if not totais:
        return {}

    consumidos_query = (
        select(
            ConciliacaoFaturamentoRemessa.cd_remessa,
            func.sum(
                case(
                    (
                        ConciliacaoFaturamentoRemessa.valor_alocado_nfse > 0,
                        ConciliacaoFaturamentoRemessa.valor_alocado_nfse
                        + ConciliacaoFaturamentoRemessa.valor_glosado,
                    ),
                    else_=ConciliacaoFaturamentoRemessa.valor_total,
                )
            ).label('valor_consumido'),
        )
        .join(
            ConciliacaoFaturamento,
            ConciliacaoFaturamento.id
            == ConciliacaoFaturamentoRemessa.conciliacao_id,
        )
        .where(
            ConciliacaoFaturamentoRemessa.tp_conciliacao == 'recurso',
            ConciliacaoFaturamentoRemessa.cd_remessa.in_(totais),
            ConciliacaoFaturamento.ativo.is_(True),
        )
        .group_by(ConciliacaoFaturamentoRemessa.cd_remessa)
    )
    consumidos = {
        int(row.cd_remessa): _money(row.valor_consumido)
        for row in session.execute(consumidos_query).all()
    }

    recursos_disponiveis = {}
    for cd_remessa, (valor_recursado_total, valor_recursado_aberto) in (
        totais.items()
    ):
        saldo_acumulado = valor_recursado_total - consumidos.get(
            cd_remessa,
            Decimal('0.00'),
        )
        valor_disponivel = min(saldo_acumulado, valor_recursado_aberto)
        if valor_disponivel > 0:
            recursos_disponiveis[cd_remessa] = _money(valor_disponivel)
    return recursos_disponiveis


def _enriquecer_remessas_com_recurso(
    remessas: list[dict],
    recursos_abertos: dict[int, Decimal],
    saldos_recebimento: dict[int, Decimal] | None = None,
    valores_acatados: dict[int, Decimal] | None = None,
) -> None:
    saldos_recebimento = saldos_recebimento or {}
    valores_acatados = valores_acatados or {}
    for remessa in remessas:
        cd_remessa = remessa['cd_remessa']
        valor_original = _money(remessa['valor_total'])
        valor_recursado = recursos_abertos.get(
            cd_remessa,
            Decimal('0.00'),
        )
        valor_acatado = valores_acatados.get(
            cd_remessa,
            Decimal('0.00'),
        )
        remessa['possui_recurso_aberto'] = cd_remessa in recursos_abertos
        remessa['valor_recursado'] = valor_recursado
        remessa['tp_conciliacao'] = 'faturamento'
        remessa['valor_remessa_original'] = None
        remessa['valor_recebimento_pendente'] = Decimal('0.00')
        remessa['valor_total_acatado'] = valor_acatado
        remessa['saldo_cobravel'] = saldos_recebimento.get(
            cd_remessa,
            max(
                valor_original - valor_acatado,
                Decimal('0.00'),
            ),
        )
        remessa['valor_elegivel_conciliacao'] = valor_original
        remessa['situacao_financeira'] = 'aberta'
        if cd_remessa in recursos_abertos:
            remessa['tp_conciliacao'] = 'recurso'
            remessa['valor_remessa_original'] = valor_original
            remessa['valor_total'] = valor_recursado
            remessa['valor_recebimento_pendente'] = valor_recursado
            remessa['valor_elegivel_conciliacao'] = valor_recursado
            remessa['situacao_financeira'] = (
                'recurso_aberto_com_acato_parcial'
                if valor_acatado > 0
                else 'recurso_aberto'
            )


def _validar_dados_bancarios(
    payload: (
        ConciliacaoFaturamentoCreate
        | RecebimentoRemessaCreate
        | RecebimentoRemessaUpdate
        | NfseConciliacaoRemessaInput
    ),
    session_postgres: Session,
    session_oracle: Session,
    lancamento_extrato_id_atual: int | None = None,
) -> LancamentoExtratoBancario | None:
    if payload.conta_bancaria_id is not None:
        try:
            conta_bancaria = session_oracle.scalar(
                select(ModelHpcContaBancaria).where(
                    ModelHpcContaBancaria.cd_con_cor
                    == payload.conta_bancaria_id
                )
            )
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                detail='Nao foi possivel validar a conta bancaria no Oracle.',
            ) from exc
        if conta_bancaria is None:
            raise HTTPException(
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                detail='Conta bancaria de recebimento invalida.',
            )

    if payload.lancamento_extrato_id is None:
        return None

    lancamento = session_postgres.get(
        LancamentoExtratoBancario,
        payload.lancamento_extrato_id,
    )
    if (
        lancamento is None
        or (
            lancamento.conciliado
            and lancamento.id != lancamento_extrato_id_atual
        )
        or lancamento.conta_bancaria_id != payload.conta_bancaria_id
        or lancamento.data_lancamento != payload.data_recebimento
    ):
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                'Lancamento do extrato invalido, ja conciliado ou '
                'incompativel com a conta/data informada.'
            ),
        )
    return lancamento


def _total_recebido_remessa(session: Session, cd_remessa: int) -> Decimal:
    return _money(
        session.scalar(
            select(func.sum(RecebimentoRemessa.valor_recebido)).where(
                RecebimentoRemessa.cd_remessa == cd_remessa
            )
        )
    )


def _total_recebido_vinculo(
    session: Session,
    conciliacao_id: int,
    cd_remessa: int,
) -> Decimal:
    return _money(
        session.scalar(
            select(func.sum(RecebimentoRemessa.valor_recebido)).where(
                RecebimentoRemessa.conciliacao_id == conciliacao_id,
                RecebimentoRemessa.cd_remessa == cd_remessa,
            )
        )
    )


def _obter_ou_criar_remessa_financeira(
    session: Session,
    remessa: dict,
) -> RemessaFinanceira:
    cd_remessa = int(remessa['cd_remessa'])
    remessa_financeira = session.scalar(
        select(RemessaFinanceira)
        .where(RemessaFinanceira.cd_remessa == cd_remessa)
        .with_for_update()
    )
    valor_original = remessa.get('valor_remessa_original')
    valor_total = _money(
        valor_original
        if valor_original is not None
        else remessa['valor_total']
    )
    if remessa_financeira is None:
        remessa_financeira = RemessaFinanceira(
            cd_remessa=cd_remessa,
            convenio=remessa['convenio'],
            cnpj_convenio=remessa['cnpj_convenio'],
            valor_total=valor_total,
            data_competencia=remessa.get('data_competencia'),
        )
        remessa_financeira.data_registro = datetime.now(
            ZoneInfo('America/Sao_Paulo')
        ).replace(tzinfo=None)
        session.add(remessa_financeira)
        session.flush()
        return remessa_financeira

    remessa_financeira.convenio = remessa['convenio']
    remessa_financeira.cnpj_convenio = remessa['cnpj_convenio']
    if remessa.get('data_competencia') is not None:
        remessa_financeira.data_competencia = remessa['data_competencia']
    if valor_total != _money(remessa_financeira.valor_total):
        remessa_financeira.valor_total = valor_total
        valor_total_recebido = _total_recebido_remessa(
            session,
            cd_remessa,
        )
        remessa_financeira.recebimento_integral = (
            valor_total_recebido > 0
            and valor_total_recebido >= valor_total
        )
    return remessa_financeira


def _obter_ou_criar_processo_remessa(
    session: Session,
    remessa: RemessaFinanceira,
    processo_recebimento: str,
    usuario_id: int,
) -> ProcessoConciliacaoRemessa:
    processo = session.scalar(
        select(ProcessoConciliacaoRemessa)
        .where(
            ProcessoConciliacaoRemessa.cd_remessa == remessa.cd_remessa
        )
        .with_for_update()
    )
    if processo is not None:
        if processo.processo_recebimento != processo_recebimento:
            conciliacoes_ativas = session.scalar(
                select(func.count(ConciliacaoFaturamentoRemessa.id))
                .join(
                    ConciliacaoFaturamento,
                    ConciliacaoFaturamento.id
                    == ConciliacaoFaturamentoRemessa.conciliacao_id,
                )
                .where(
                    ConciliacaoFaturamentoRemessa.cd_remessa
                    == remessa.cd_remessa,
                    ConciliacaoFaturamento.ativo.is_(True),
                )
            )
            if conciliacoes_ativas:
                raise HTTPException(
                    status_code=HTTPStatus.CONFLICT,
                    detail=(
                        f'A remessa {remessa.cd_remessa} ja utiliza o '
                        f'processo de recebimento '
                        f'{processo.processo_recebimento}. Todas as NFS-e '
                        'da remessa devem usar o mesmo processo.'
                    ),
                )
            processo.processo_recebimento = processo_recebimento
            processo.usuario_atualizacao_id = usuario_id
            processo.data_atualizacao = datetime.now(
                ZoneInfo('America/Sao_Paulo')
            ).replace(tzinfo=None)
        return processo

    processo = ProcessoConciliacaoRemessa(
        cd_remessa=remessa.cd_remessa,
        processo_recebimento=processo_recebimento,
        usuario_id=usuario_id,
    )
    processo.data_criacao = datetime.now(
        ZoneInfo('America/Sao_Paulo')
    ).replace(tzinfo=None)
    session.add(processo)
    session.flush()
    return processo


def _registrar_itens_glosa_conciliacao(
    session: Session,
    conciliacao: ConciliacaoFaturamento,
    remessa_conciliada: ConciliacaoFaturamentoRemessa,
    itens: list[dict],
) -> None:
    data_glosa = (
        conciliacao.data_recebimento or conciliacao.data_criacao.date()
    )
    for item in itens:
        descricao_item = str(
            item.get('descricao_item') or 'Item da remessa'
        ).strip()
        registro = RegistroGlosa(
            codigo_paciente=item['codigo_paciente'],
            nm_paciente=item['nm_paciente'],
            cd_remessa=remessa_conciliada.cd_remessa,
            cd_atendimento=item['cd_atendimento'],
            conta=item['conta'],
            cd_prestador=item['cd_prestador'],
            cd_convenio=item['cd_convenio'],
            tp_atendimento=item['tp_atendimento'],
            procedimento=item['procedimento'],
            convenio=item['convenio'],
            guia=item['guia'],
            prestador=item['prestador'],
            data_atendimento=item['data_atendimento'],
            valor=_money(item['valor']),
            processo_controle_fatura_gab=(
                conciliacao.processo_recebimento
            ),
            processo_recurso=None,
            data_glosa=data_glosa,
            motivo_glosa='Glosa informada na conciliacao fiscal',
            descricao_glosa=(
                f'{descricao_item}. Pendente de tratativa da NFS-e '
                f'{conciliacao.numero_nfse}.'
            ),
            qtd_recursado=None,
            valor_recursado=None,
            dt_recurso=None,
            dt_pagamento=conciliacao.data_recebimento,
            dt_recebimento=None,
            valor_recebido=None,
            qtd_recebida=None,
            observacao_recebimento=None,
            cd_lancamento=item['cd_lancamento'],
            qtd_registro=item['qtd_registro'],
            descricao_item=item['descricao_item'],
            data_alta=item['data_alta'],
            data_lancamento=item['data_lancamento'],
            cd_gru_pro=item['cd_gru_pro'],
            ds_gru_pro=item['ds_gru_pro'],
            cd_gru_fat=item['cd_gru_fat'],
            ds_gru_fat=item['ds_gru_fat'],
            conciliacao_remessa_id=remessa_conciliada.id,
            origem_registro='conciliacao',
            sn_glosado='true',
            sn_ativo='true',
        )
        registro.data_criacao = conciliacao.data_criacao
        session.add(registro)


def _carregar_itens_glosa_conciliacao(
    session_oracle: Session,
    cnpj_convenio: str,
    ids_remessas: set[int],
) -> dict[int, list[dict]]:
    itens_por_remessa: dict[int, list[dict]] = {
        cd_remessa: [] for cd_remessa in ids_remessas
    }
    if not ids_remessas:
        return itens_por_remessa
    try:
        itens_glosa = _consultar_itens_remessas_hpc(
            session_oracle,
            cnpj_convenio,
            ids_remessas,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail=(
                'Nao foi possivel carregar os itens das remessas glosadas '
                'no Oracle.'
            ),
        ) from exc
    for item_glosa in itens_glosa:
        itens_por_remessa[item_glosa['cd_remessa']].append(item_glosa)

    remessas_sem_itens = sorted(
        cd_remessa
        for cd_remessa, itens in itens_por_remessa.items()
        if not itens
    )
    if remessas_sem_itens:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                'Nao foram encontrados itens analiticos no Oracle para as '
                'remessas glosadas: '
                + ', '.join(str(item) for item in remessas_sem_itens)
                + '.'
            ),
        )
    return itens_por_remessa


def _registrar_recebimento_remessa(  # noqa: PLR0913
    session: Session,
    remessa: RemessaFinanceira,
    conciliacao_id: int,
    numero_nfse: str,
    data_recebimento: date,
    valor_recebido: Decimal,
    usuario_id: int,
    conta_bancaria_id: int,
    conta_plano_contas: str | None,
    conta_centro_custo: str | None,
    lancamento_extrato_id: int | None,
) -> tuple[RecebimentoRemessa, Decimal]:
    valor_recebido = _money(valor_recebido)
    if valor_recebido <= 0:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='O valor recebido deve ser maior que zero.',
        )

    valor_total_recebido = _total_recebido_remessa(
        session,
        remessa.cd_remessa,
    ) + valor_recebido
    valor_total_remessa = _money(remessa.valor_total)
    valor_total_acatado = _valores_acatados_por_remessa(
        session,
        {remessa.cd_remessa},
    ).get(remessa.cd_remessa, Decimal('0.00'))
    valor_maximo_recebivel = max(
        valor_total_remessa - valor_total_acatado,
        Decimal('0.00'),
    )
    if valor_total_recebido > valor_maximo_recebivel:
        saldo = max(
            valor_maximo_recebivel
            - (valor_total_recebido - valor_recebido),
            Decimal('0.00'),
        )
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                f'O valor recebido da remessa {remessa.cd_remessa} excede '
                f'o saldo em aberto de {_valor_reais_mensagem(saldo)}.'
            ),
        )

    recebimento_integral = valor_total_recebido == valor_total_remessa
    recebimento = RecebimentoRemessa(
        cd_remessa=remessa.cd_remessa,
        conciliacao_id=conciliacao_id,
        numero_nfse=numero_nfse,
        data_recebimento=data_recebimento,
        valor_recebido=valor_recebido,
        usuario_id=usuario_id,
        conta_bancaria_id=conta_bancaria_id,
        recebimento_integral=recebimento_integral,
        conta_plano_contas=conta_plano_contas,
        conta_centro_custo=conta_centro_custo,
        lancamento_extrato_id=lancamento_extrato_id,
    )
    recebimento.data_registro = datetime.now(
        ZoneInfo('America/Sao_Paulo')
    ).replace(tzinfo=None)
    remessa.recebimento_integral = recebimento_integral
    session.add(recebimento)
    session.flush()
    return recebimento, valor_total_recebido


def _recebimento_remessa_publico(
    recebimento: RecebimentoRemessa,
    remessa: RemessaFinanceira,
    valor_total_recebido: Decimal,
    valor_total_acatado: Decimal = Decimal('0.00'),
) -> dict:
    valor_total_remessa = _money(remessa.valor_total)
    valor_total_recebido = _money(valor_total_recebido)
    valor_total_acatado = _money(valor_total_acatado)
    saldo_em_aberto = max(
        valor_total_remessa - valor_total_recebido - valor_total_acatado,
        Decimal('0.00'),
    )
    return {
        'id': recebimento.id,
        'cd_remessa': recebimento.cd_remessa,
        'conciliacao_id': recebimento.conciliacao_id,
        'numero_nfse': recebimento.numero_nfse,
        'data_recebimento': recebimento.data_recebimento,
        'valor_recebido': _money(recebimento.valor_recebido),
        'usuario_id': recebimento.usuario_id,
        'conta_bancaria_id': recebimento.conta_bancaria_id,
        'conta_plano_contas': recebimento.conta_plano_contas,
        'conta_centro_custo': recebimento.conta_centro_custo,
        'lancamento_extrato_id': recebimento.lancamento_extrato_id,
        'data_registro': recebimento.data_registro,
        'recebimento_integral': recebimento.recebimento_integral,
        'remessa_recebida_integralmente': remessa.recebimento_integral,
        'remessa_encerrada_financeiramente': (
            remessa.recebimento_integral
            or (valor_total_acatado > 0 and saldo_em_aberto == 0)
        ),
        'valor_total_remessa': valor_total_remessa,
        'valor_total_recebido': valor_total_recebido,
        'valor_total_acatado': valor_total_acatado,
        'saldo_em_aberto': saldo_em_aberto,
    }


def _contexto_recebimento_remessa(
    session: Session,
    recebimento_id: int,
) -> tuple[
    RecebimentoRemessa,
    ConciliacaoFaturamento,
    ConciliacaoFaturamentoRemessa,
    RemessaFinanceira,
]:
    recebimento = session.scalar(
        select(RecebimentoRemessa)
        .where(RecebimentoRemessa.id == recebimento_id)
        .with_for_update()
    )
    if recebimento is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Recebimento financeiro não encontrado.',
        )
    conciliacao = session.get(
        ConciliacaoFaturamento,
        recebimento.conciliacao_id,
    )
    vinculo = session.scalar(
        select(ConciliacaoFaturamentoRemessa).where(
            ConciliacaoFaturamentoRemessa.conciliacao_id
            == recebimento.conciliacao_id,
            ConciliacaoFaturamentoRemessa.cd_remessa
            == recebimento.cd_remessa,
        )
    )
    remessa = session.get(RemessaFinanceira, recebimento.cd_remessa)
    if conciliacao is None or vinculo is None or remessa is None:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='O recebimento não possui uma conciliação válida.',
        )
    return recebimento, conciliacao, vinculo, remessa


def _sincronizar_estado_recebimentos(
    session: Session,
    conciliacao: ConciliacaoFaturamento,
    remessa: RemessaFinanceira,
) -> Decimal:
    session.flush()
    vinculos = list(
        session.scalars(
            select(ConciliacaoFaturamentoRemessa).where(
                ConciliacaoFaturamentoRemessa.conciliacao_id
                == conciliacao.id
            )
        )
    )
    recebimentos_conciliacao = list(
        session.scalars(
            select(RecebimentoRemessa)
            .where(RecebimentoRemessa.conciliacao_id == conciliacao.id)
            .order_by(
                RecebimentoRemessa.data_recebimento,
                RecebimentoRemessa.id,
            )
        )
    )
    totais_por_remessa = {
        cd_remessa: _money(valor)
        for cd_remessa, valor in session.execute(
            select(
                RecebimentoRemessa.cd_remessa,
                func.sum(RecebimentoRemessa.valor_recebido),
            )
            .where(RecebimentoRemessa.conciliacao_id == conciliacao.id)
            .group_by(RecebimentoRemessa.cd_remessa)
        )
    }
    conciliacao_integral = bool(vinculos) and all(
        totais_por_remessa.get(vinculo.cd_remessa, Decimal('0.00'))
        == _valor_alocado_vinculo(vinculo)
        for vinculo in vinculos
    )
    if conciliacao_integral and recebimentos_conciliacao:
        ultimo = recebimentos_conciliacao[-1]
        conciliacao.data_recebimento = ultimo.data_recebimento
        conciliacao.conta_bancaria_id = ultimo.conta_bancaria_id
        conciliacao.conta_plano_contas = ultimo.conta_plano_contas
        conciliacao.conta_centro_custo = ultimo.conta_centro_custo
        conciliacao.lancamento_extrato_id = ultimo.lancamento_extrato_id
    else:
        conciliacao.data_recebimento = None
        conciliacao.conta_bancaria_id = None
        conciliacao.conta_plano_contas = None
        conciliacao.conta_centro_custo = None
        conciliacao.lancamento_extrato_id = None

    recebimentos_remessa = list(
        session.scalars(
            select(RecebimentoRemessa)
            .where(RecebimentoRemessa.cd_remessa == remessa.cd_remessa)
            .order_by(
                RecebimentoRemessa.data_recebimento,
                RecebimentoRemessa.id,
            )
        )
    )
    valor_total_recebido = _money(
        sum(
            (item.valor_recebido for item in recebimentos_remessa),
            Decimal('0.00'),
        )
    )
    remessa_integral = valor_total_recebido == _money(remessa.valor_total)
    remessa.recebimento_integral = remessa_integral
    for item in recebimentos_remessa:
        item.recebimento_integral = False
    if remessa_integral and recebimentos_remessa:
        recebimentos_remessa[-1].recebimento_integral = True
    return valor_total_recebido


def _liberar_lancamento_financeiro(
    session: Session,
    lancamento_extrato_id: int | None,
) -> None:
    if lancamento_extrato_id is None:
        return
    ainda_utilizado = session.scalar(
        select(RecebimentoRemessa.id)
        .where(
            RecebimentoRemessa.lancamento_extrato_id
            == lancamento_extrato_id
        )
        .limit(1)
    )
    if ainda_utilizado is None:
        lancamento = session.get(
            LancamentoExtratoBancario,
            lancamento_extrato_id,
        )
        if lancamento is not None:
            lancamento.conciliado = False


def _carregar_remessas_para_conciliacao(
    payload: ConciliacaoFaturamentoCreate,
    cnpj_convenio: str,
    session_postgres: Session,
    session_oracle: Session,
) -> dict[int, dict]:
    ids_remessa = [item.cd_remessa for item in payload.remessas]
    if len(ids_remessa) != len(set(ids_remessa)):
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                'Uma mesma remessa nao pode ser adicionada mais de uma vez.'
            ),
        )

    ids_remessa_set = set(ids_remessa)
    remessas_recebidas_integralmente = _remessas_conciliadas(
        session_postgres
    )
    remessas_previamente_conciliadas = _remessas_previamente_conciliadas(
        session_postgres,
        ids_remessa_set,
    )
    recursos_abertos = _recursos_abertos_por_remessa(
        session_postgres,
        ids_remessa_set,
    )
    valores_acatados = _valores_acatados_por_remessa(
        session_postgres,
        ids_remessa_set,
    )
    saldos_recebimento = _saldos_recebimento_por_remessa(
        session_postgres,
        ids_remessa_set,
        valores_acatados,
    )
    remessas_encerradas_por_acato = _remessas_encerradas_por_acato(
        session_postgres,
        valores_acatados,
        saldos_recebimento,
    )
    restricoes = _restricoes_nova_conciliacao(
        remessas_previamente_conciliadas,
        remessas_recebidas_integralmente.intersection(ids_remessa_set),
        remessas_encerradas_por_acato,
        recursos_abertos,
        valores_acatados,
    )
    if restricoes:
        cd_remessa = min(restricoes)
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail=restricoes[cd_remessa],
        )

    try:
        remessas_hpc = _consultar_remessas_hpc(
            session_oracle,
            cnpj_convenio,
            set(restricoes),
            cd_remessas=ids_remessa_set,
            limit=len(ids_remessa),
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel validar as remessas no Oracle.',
        ) from exc

    remessas_por_id = {
        remessa['cd_remessa']: remessa for remessa in remessas_hpc
    }
    if set(ids_remessa) != set(remessas_por_id):
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                'Uma ou mais remessas nao pertencem ao convenio da NFS-e '
                'ou nao estao disponiveis para conciliacao.'
            ),
        )
    _enriquecer_remessas_com_recurso(
        remessas_hpc,
        recursos_abertos,
        saldos_recebimento,
        valores_acatados,
    )
    return remessas_por_id


def _calcular_totais_conciliacao(
    payload: ConciliacaoFaturamentoCreate,
    remessas_por_id: dict[int, dict],
    recursos_abertos: dict[int, Decimal],
) -> tuple[Decimal, Decimal]:
    total_remessas = Decimal('0.00')
    total_glosas = Decimal('0.00')
    for item in payload.remessas:
        valor_total = _money(remessas_por_id[item.cd_remessa]['valor_total'])
        tp_conciliacao = remessas_por_id[item.cd_remessa].get(
            'tp_conciliacao',
            'faturamento',
        )
        valor_glosado = (
            _money(item.valor_glosado)
            if tp_conciliacao == 'recurso'
            else recursos_abertos.get(
                item.cd_remessa,
                _money(item.valor_glosado),
            )
        )
        if valor_glosado > valor_total:
            tipo_valor = (
                'glosado no recurso'
                if tp_conciliacao == 'recurso'
                else 'recursado'
                if item.cd_remessa in recursos_abertos
                else 'glosado'
            )
            raise HTTPException(
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                detail=(
                    f'O valor {tipo_valor} da remessa {item.cd_remessa} nao '
                    'pode ser maior que o valor total da remessa.'
                ),
            )
        total_remessas += valor_total
        total_glosas += valor_glosado
    return total_remessas.quantize(CENTAVOS), total_glosas.quantize(CENTAVOS)


def _nota_pendente_query(row_hash: str | None = None):
    query = select(NfseXml).where(
        or_(
            NfseXml.cancelamento_codigo.is_(None),
            NfseXml.cancelamento_codigo == '',
        ),
        ~select(ConciliacaoFaturamento.id)
        .where(
            ConciliacaoFaturamento.ativo.is_(True),
            or_(
                ConciliacaoFaturamento.nfse_row_hash == NfseXml.row_hash,
                ConciliacaoFaturamento.numero_nfse == NfseXml.numero_nfse,
            )
        )
        .exists(),
    )
    if row_hash is not None:
        query = query.where(NfseXml.row_hash == row_hash)
    return query


def _nfses_unicas_query(query):
    ranking = query.with_only_columns(
        NfseXml.row_hash.label('row_hash'),
        func
        .row_number()
        .over(
            partition_by=(
                NfseXml.numero_nfse,
                NfseXml.prestador_cnpj,
            ),
            order_by=(
                NfseXml.data_hora.desc().nulls_last(),
                NfseXml.row_hash.desc(),
            ),
        )
        .label('ordem_duplicidade'),
    ).subquery()
    return (
        select(NfseXml)
        .join(ranking, ranking.c.row_hash == NfseXml.row_hash)
        .where(ranking.c.ordem_duplicidade == 1)
    )


def _resumos_remessas(
    session: Session,
    cd_remessas: set[int] | None = None,
) -> dict[int, dict]:
    query = (
        select(
            ConciliacaoFaturamentoRemessa,
            ConciliacaoFaturamento,
            ProcessoConciliacaoRemessa,
            NfseXml.data_hora,
        )
        .join(
            ConciliacaoFaturamento,
            ConciliacaoFaturamento.id
            == ConciliacaoFaturamentoRemessa.conciliacao_id,
        )
        .outerjoin(
            ProcessoConciliacaoRemessa,
            ProcessoConciliacaoRemessa.id
            == ConciliacaoFaturamentoRemessa.processo_remessa_id,
        )
        .outerjoin(
            NfseXml,
            NfseXml.row_hash == ConciliacaoFaturamento.nfse_row_hash,
        )
        .where(ConciliacaoFaturamento.ativo.is_(True))
    )
    if cd_remessas is not None:
        if not cd_remessas:
            return {}
        query = query.where(
            ConciliacaoFaturamentoRemessa.cd_remessa.in_(cd_remessas)
        )
    rows = session.execute(
        query.order_by(
            ConciliacaoFaturamentoRemessa.cd_remessa,
            ConciliacaoFaturamento.data_criacao.desc(),
            ConciliacaoFaturamentoRemessa.id.desc(),
        )
    ).all()
    resumos: dict[int, dict] = {}
    for vinculo, conciliacao, processo, data_emissao in rows:
        resumo = resumos.setdefault(
            int(vinculo.cd_remessa),
            {
                'valor_conciliado': Decimal('0.00'),
                'valor_impostos': Decimal('0.00'),
                'valor_glosado': Decimal('0.00'),
                'valor_recurso_consumido': Decimal('0.00'),
                'processo_recebimento': None,
                'historico': [],
                'numeros_nfse': set(),
            },
        )
        valor_alocado = _valor_alocado_vinculo(vinculo)
        valor_impostos = _valor_impostos_vinculo(vinculo)
        valor_glosado = _money(vinculo.valor_glosado)
        resumo['valor_conciliado'] += valor_alocado + valor_impostos
        resumo['valor_impostos'] += valor_impostos
        resumo['valor_glosado'] += valor_glosado
        if vinculo.tp_conciliacao == 'recurso':
            resumo['valor_recurso_consumido'] += (
                valor_alocado + valor_glosado
            )
        resumo['processo_recebimento'] = (
            resumo['processo_recebimento']
            or (
                processo.processo_recebimento
                if processo is not None
                else conciliacao.processo_recebimento
            )
        )
        resumo['numeros_nfse'].add(conciliacao.numero_nfse)
        resumo['historico'].append(
            {
                'id': vinculo.id,
                'numero_nfse': conciliacao.numero_nfse,
                'data_emissao': data_emissao,
                'valor_nfse': _money(conciliacao.valor_nfse),
                'valor_alocado': valor_alocado,
                'valor_impostos': valor_impostos,
                'valor_glosado': valor_glosado,
                'tipo_conciliacao': vinculo.tp_conciliacao,
                'data_previsao_recebimento': (
                    conciliacao.data_previsao_recebimento
                ),
                'data_recebimento': conciliacao.data_recebimento,
                'conta_bancaria_id': conciliacao.conta_bancaria_id,
                'data_conciliacao': conciliacao.data_criacao,
            }
        )

    return resumos


def _posicao_remessa(
    remessa: dict,
    resumo: dict | None,
    valor_acatado: Decimal,
    recurso_disponivel: Decimal,
) -> dict:
    resumo = resumo or {}
    valor_remessa = _money(remessa['valor_total'])
    valor_conciliado = _money(resumo.get('valor_conciliado'))
    valor_glosado = _money(resumo.get('valor_glosado'))
    recurso_consumido = _money(resumo.get('valor_recurso_consumido'))
    saldo_base = max(
        valor_remessa
        - valor_conciliado
        - valor_glosado
        - valor_acatado,
        Decimal('0.00'),
    )
    glosa_pendente = max(
        valor_glosado - recurso_consumido - valor_acatado,
        Decimal('0.00'),
    )
    valor_nao_conciliado = max(
        saldo_base,
        glosa_pendente,
        Decimal('0.00'),
    )
    valor_livre = max(saldo_base - glosa_pendente, Decimal('0.00'))
    valor_disponivel = min(
        valor_nao_conciliado,
        valor_livre + min(glosa_pendente, recurso_disponivel),
    )
    return {
        'cd_remessa': remessa['cd_remessa'],
        'data_competencia': remessa.get('data_competencia'),
        'convenio': remessa['convenio'],
        'cnpj_convenio': remessa['cnpj_convenio'],
        'valor_remessa': valor_remessa,
        'valor_conciliado': valor_conciliado,
        'valor_impostos': _money(resumo.get('valor_impostos')),
        'valor_acatado': _money(valor_acatado),
        'valor_nao_conciliado': _money(valor_nao_conciliado),
        'valor_recurso_disponivel': _money(recurso_disponivel),
        'valor_disponivel_conciliacao': _money(valor_disponivel),
        'processo_recebimento': resumo.get('processo_recebimento'),
        'historico': resumo.get('historico', []),
    }


def _codigos_remessas_encerradas(session: Session) -> set[int]:
    remessas = list(session.scalars(select(RemessaFinanceira)))
    if not remessas:
        return set()
    ids = {remessa.cd_remessa for remessa in remessas}
    resumos = _resumos_remessas(session, ids)
    acatados = _valores_acatados_por_remessa(session, ids)
    recursos = _recursos_abertos_por_remessa(session, ids)
    return {
        remessa.cd_remessa
        for remessa in remessas
        if _posicao_remessa(
            {
                'cd_remessa': remessa.cd_remessa,
                'data_competencia': remessa.data_competencia,
                'convenio': remessa.convenio,
                'cnpj_convenio': remessa.cnpj_convenio,
                'valor_total': remessa.valor_total,
            },
            resumos.get(remessa.cd_remessa),
            acatados.get(remessa.cd_remessa, Decimal('0.00')),
            recursos.get(remessa.cd_remessa, Decimal('0.00')),
        )['valor_nao_conciliado']
        <= 0
    }


def _valores_utilizados_nfse(
    session: Session,
) -> dict[tuple[str, str], Decimal]:
    rows = session.execute(
        select(
            ConciliacaoFaturamento,
            ConciliacaoFaturamentoRemessa,
        ).join(
            ConciliacaoFaturamentoRemessa,
            ConciliacaoFaturamentoRemessa.conciliacao_id
            == ConciliacaoFaturamento.id,
        )
        .where(ConciliacaoFaturamento.ativo.is_(True))
    ).all()
    utilizados: dict[tuple[str, str], Decimal] = {}
    for conciliacao, vinculo in rows:
        chave = (
            str(conciliacao.numero_nfse),
            _normalize_cnpj(conciliacao.cnpj_convenio),
        )
        utilizados[chave] = utilizados.get(
            chave,
            Decimal('0.00'),
        ) + _valor_alocado_vinculo(vinculo)
    return utilizados


def _valores_impostos_utilizados_nfse(
    session: Session,
) -> dict[tuple[str, str], Decimal]:
    rows = session.execute(
        select(
            ConciliacaoFaturamento,
            ConciliacaoFaturamentoRemessa,
        ).join(
            ConciliacaoFaturamentoRemessa,
            ConciliacaoFaturamentoRemessa.conciliacao_id
            == ConciliacaoFaturamento.id,
        )
        .where(ConciliacaoFaturamento.ativo.is_(True))
    ).all()
    utilizados: dict[tuple[str, str], Decimal] = {}
    for conciliacao, vinculo in rows:
        chave = (
            str(conciliacao.numero_nfse),
            _normalize_cnpj(conciliacao.cnpj_convenio),
        )
        utilizados[chave] = utilizados.get(
            chave,
            Decimal('0.00'),
        ) + _valor_impostos_vinculo(vinculo)
    return utilizados


def _consultar_nfses_com_saldo_para_remessa(  # noqa: PLR0913
    session: Session,
    remessa: dict,
    resumo: dict,
    valor_disponivel: Decimal,
    q: str | None,
    limit: int,
) -> list[dict]:
    cnpj = _normalize_cnpj(remessa['cnpj_convenio'])
    query = select(NfseXml).where(
        or_(
            NfseXml.cancelamento_codigo.is_(None),
            NfseXml.cancelamento_codigo == '',
        ),
        or_(
            func.regexp_replace(
                NfseXml.prestador_cnpj,
                '[^0-9]',
                '',
                'g',
            )
            == cnpj,
            func.regexp_replace(
                NfseXml.tomador_cnpj,
                '[^0-9]',
                '',
                'g',
            )
            == cnpj,
        ),
    )
    termo = (q or '').strip()
    if termo:
        query = query.where(
            or_(
                NfseXml.numero_nfse.ilike(f'%{termo}%'),
                NfseXml.tomador_razao_social.ilike(f'%{termo}%'),
            )
        )
    notas = session.scalars(
        _nfses_unicas_query(query).order_by(
            NfseXml.data_hora.desc(),
            NfseXml.numero_nfse.desc(),
        )
    ).all()
    utilizados = _valores_utilizados_nfse(session)
    impostos_utilizados = _valores_impostos_utilizados_nfse(session)
    numeros_ja_usados = resumo.get('numeros_nfse', set())
    resultado = []
    for nota in notas:
        numero_nfse = str(nota.numero_nfse or '-')
        if numero_nfse in numeros_ja_usados:
            continue
        valor_nfse = _money(nota.valor_liquido_nfse)
        valor_utilizado = utilizados.get(
            (numero_nfse, cnpj),
            Decimal('0.00'),
        )
        nota_publica = _nota_publica(
            nota,
            {
                'convenio': remessa['convenio'],
                'cnpj_convenio': cnpj,
            },
        )
        impostos = _money(nota_publica['impostos'])
        imposto_utilizado = impostos_utilizados.get(
            (numero_nfse, cnpj),
            Decimal('0.00'),
        )
        saldo_nfse = max(
            valor_nfse - valor_utilizado,
            Decimal('0.00'),
        )
        saldo_impostos = max(
            impostos - imposto_utilizado,
            Decimal('0.00'),
        )
        if saldo_nfse <= 0:
            continue
        resultado.append(
            {
                'row_hash': nota.row_hash,
                'numero_nfse': numero_nfse,
                'data_emissao': nota.data_hora,
                'convenio': remessa['convenio'],
                'cnpj_convenio': cnpj,
                'valor_nfse': valor_nfse,
                'valor_utilizado': _money(valor_utilizado),
                'saldo_nfse': _money(saldo_nfse),
                'impostos': impostos,
                'impostos_utilizados': _money(imposto_utilizado),
                'saldo_impostos': _money(saldo_impostos),
                'valor_sugerido': _money(
                    min(saldo_nfse, valor_disponivel)
                ),
            }
        )
        if len(resultado) >= limit:
            break
    return resultado


@router.get(
    '/conciliacao-faturamento/remessas',
    status_code=HTTPStatus.OK,
    response_model=RemessasFaturamentoList,
)
def consultar_remessas_faturamento(  # noqa: PLR0913
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
    session_oracle: Session = Depends(get_session_oracle),
    q: str | None = Query(default=None, max_length=100),
    numero_nfse: str | None = None,
    cd_remessa: str | None = None,
    convenio: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    try:
        sincronizar_totais_remessas_financeiras(
            session_postgres,
            session_oracle,
        )
        remessas, total = _consultar_cards_remessas_hpc(
            session_oracle,
            _codigos_remessas_encerradas(session_postgres),
            q=q,
            numero_remessa=cd_remessa,
            convenio=convenio,
            cd_remessas_nfse=_codigos_remessas_por_nfse(
                session_postgres,
                numero_nfse,
            ),
            limit=limit,
            offset=offset,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel consultar a HPC_V_CONTA_ATENDIMENTO.',
        ) from exc
    ids = {remessa['cd_remessa'] for remessa in remessas}
    resumos = _resumos_remessas(session_postgres, ids)
    acatados = _valores_acatados_por_remessa(session_postgres, ids)
    recursos = _recursos_abertos_por_remessa(session_postgres, ids)
    cards = [
        _posicao_remessa(
            remessa,
            resumos.get(remessa['cd_remessa']),
            acatados.get(remessa['cd_remessa'], Decimal('0.00')),
            recursos.get(remessa['cd_remessa'], Decimal('0.00')),
        )
        for remessa in remessas
    ]
    return {
        'remessas': cards,
        'total': total,
        'valor_total_conciliado': _money(
            sum(
                (card['valor_conciliado'] for card in cards),
                Decimal('0.00'),
            )
        ),
        'valor_total_nao_conciliado': _money(
            sum(
                (card['valor_nao_conciliado'] for card in cards),
                Decimal('0.00'),
            )
        ),
        'limit': limit,
        'offset': offset,
    }


@router.get(
    '/conciliacao-faturamento/remessas/{cd_remessa}/notas',
    status_code=HTTPStatus.OK,
    response_model=NfsesSaldoRemessaList,
)
def consultar_nfses_para_remessa(  # noqa: PLR0913
    cd_remessa: int,
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
    session_oracle: Session = Depends(get_session_oracle),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
):
    try:
        remessas, _ = _consultar_cards_remessas_hpc(
            session_oracle,
            set(),
            q=str(cd_remessa),
            limit=1,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel consultar a HPC_V_CONTA_ATENDIMENTO.',
        ) from exc
    remessa = next(
        (
            item
            for item in remessas
            if int(item['cd_remessa']) == cd_remessa
        ),
        None,
    )
    if remessa is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Remessa nao encontrada na HPC_V_CONTA_ATENDIMENTO.',
        )
    resumo = _resumos_remessas(session_postgres, {cd_remessa}).get(
        cd_remessa,
        {},
    )
    posicao = _posicao_remessa(
        remessa,
        resumo,
        _valores_acatados_por_remessa(
            session_postgres,
            {cd_remessa},
        ).get(cd_remessa, Decimal('0.00')),
        _recursos_abertos_por_remessa(
            session_postgres,
            {cd_remessa},
        ).get(cd_remessa, Decimal('0.00')),
    )
    valor_disponivel = posicao['valor_disponivel_conciliacao']
    if valor_disponivel <= 0:
        message = (
            f'A remessa {cd_remessa} possui glosa ainda sem recurso '
            'disponivel. Trate a glosa e registre o recurso antes de '
            'vincular uma nova NFS-e.'
        )
        return {
            'notas': [],
            'message': message,
            'valor_disponivel_remessa': Decimal('0.00'),
        }
    notas = _consultar_nfses_com_saldo_para_remessa(
        session_postgres,
        remessa,
        resumo,
        valor_disponivel,
        q,
        limit,
    )
    return {
        'notas': notas,
        'message': (
            None
            if notas
            else 'Nenhuma NFS-e com saldo disponivel para este convenio.'
        ),
        'valor_disponivel_remessa': valor_disponivel,
    }


@router.post(
    '/conciliacao-faturamento/remessas/{cd_remessa}/conciliar',
    status_code=HTTPStatus.CREATED,
    response_model=ConciliacaoRemessaPublic,
)
def conciliar_remessa_com_nfses(  # noqa: PLR0912, PLR0915
    cd_remessa: int,
    payload: ConciliacaoRemessaCreate,
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
    session_oracle: Session = Depends(get_session_oracle),
):
    try:
        remessas, _ = _consultar_cards_remessas_hpc(
            session_oracle,
            set(),
            q=str(cd_remessa),
            limit=1,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel validar a remessa no Oracle.',
        ) from exc
    remessa = next(
        (
            item
            for item in remessas
            if int(item['cd_remessa']) == cd_remessa
        ),
        None,
    )
    if remessa is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Remessa nao encontrada na HPC_V_CONTA_ATENDIMENTO.',
        )

    resumo = _resumos_remessas(session_postgres, {cd_remessa}).get(
        cd_remessa,
        {},
    )
    valor_acatado = _valores_acatados_por_remessa(
        session_postgres,
        {cd_remessa},
    ).get(cd_remessa, Decimal('0.00'))
    recurso_disponivel = _recursos_abertos_por_remessa(
        session_postgres,
        {cd_remessa},
    ).get(cd_remessa, Decimal('0.00'))
    posicao = _posicao_remessa(
        remessa,
        resumo,
        valor_acatado,
        recurso_disponivel,
    )
    valor_disponivel = posicao['valor_disponivel_conciliacao']
    if valor_disponivel <= 0:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail=(
                f'A remessa {cd_remessa} possui glosa ainda sem recurso '
                'disponivel. Trate a glosa e registre o recurso antes de '
                'vincular uma nova NFS-e.'
            ),
        )

    total_alocado = sum(
        (_money(item.valor_alocado) for item in payload.notas),
        Decimal('0.00'),
    )
    total_glosado = sum(
        (_money(item.valor_glosado) for item in payload.notas),
        Decimal('0.00'),
    )
    total_impostos = sum(
        (_money(item.valor_impostos) for item in payload.notas),
        Decimal('0.00'),
    )
    valor_comprometido = total_alocado + total_impostos + total_glosado
    if valor_comprometido > valor_disponivel:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                'A soma dos valores alocados, retencoes e glosados nao pode '
                f'o saldo disponivel da remessa '
                f'({_valor_reais_mensagem(valor_disponivel)}).'
            ),
        )

    utilizados_nfse = _valores_utilizados_nfse(session_postgres)
    impostos_utilizados_nfse = _valores_impostos_utilizados_nfse(
        session_postgres
    )
    notas_validadas = []
    numeros_nfse = set()
    lancamentos = []
    cnpj_remessa = _normalize_cnpj(remessa['cnpj_convenio'])
    for item in payload.notas:
        nota = session_postgres.get(NfseXml, item.nfse_row_hash)
        if nota is None or nota.cancelamento_codigo not in (None, ''):
            raise HTTPException(
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                detail='Uma das NFS-e informadas nao existe ou foi cancelada.',
            )
        cnpjs_nota = {
            _normalize_cnpj(nota.prestador_cnpj),
            _normalize_cnpj(nota.tomador_cnpj),
        }
        if cnpj_remessa not in cnpjs_nota:
            raise HTTPException(
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                detail=(
                    f'A NFS-e {nota.numero_nfse or "-"} nao pertence ao '
                    'convenio da remessa.'
                ),
            )
        numero_nfse = str(nota.numero_nfse or '-')
        if numero_nfse in numeros_nfse:
            raise HTTPException(
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                detail='Uma mesma NFS-e nao pode ser informada duas vezes.',
            )
        if numero_nfse in resumo.get('numeros_nfse', set()):
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail=(
                    f'A NFS-e {numero_nfse} já está conciliada com a remessa '
                    f'{cd_remessa}.'
                ),
            )
        numeros_nfse.add(numero_nfse)
        valor_nfse = _money(nota.valor_liquido_nfse)
        valor_utilizado = utilizados_nfse.get(
            (numero_nfse, cnpj_remessa),
            Decimal('0.00'),
        )
        saldo_nfse = max(
            valor_nfse - valor_utilizado,
            Decimal('0.00'),
        )
        if _money(item.valor_alocado) > saldo_nfse:
            raise HTTPException(
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                detail=(
                    f'O valor alocado da NFS-e {numero_nfse} nao pode '
                    f'exceder seu saldo de '
                    f'{_valor_reais_mensagem(saldo_nfse)}.'
                ),
            )
        total_impostos_nfse = _money(_nota_publica(nota)['impostos'])
        impostos_utilizados = impostos_utilizados_nfse.get(
            (numero_nfse, cnpj_remessa),
            Decimal('0.00'),
        )
        saldo_impostos = max(
            total_impostos_nfse - impostos_utilizados,
            Decimal('0.00'),
        )
        if _money(item.valor_impostos) > saldo_impostos:
            raise HTTPException(
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                detail=(
                    f'O valor de retencoes da NFS-e {numero_nfse} nao pode '
                    f'exceder seu saldo de '
                    f'{_valor_reais_mensagem(saldo_impostos)}.'
                ),
            )
        lancamento = _validar_dados_bancarios(
            item,
            session_postgres,
            session_oracle,
        )
        if lancamento is not None:
            lancamentos.append(lancamento)
        notas_validadas.append((item, nota, valor_nfse))
    itens_glosa = (
        _carregar_itens_glosa_conciliacao(
            session_oracle,
            cnpj_remessa,
            {cd_remessa},
        )
        if any(item.sn_glosado for item in payload.notas)
        else {cd_remessa: []}
    )
    tipo_conciliacao = (
        'recurso'
        if recurso_disponivel > 0 and resumo.get('valor_glosado', 0) > 0
        else 'faturamento'
    )

    try:
        remessa_financeira = _obter_ou_criar_remessa_financeira(
            session_postgres,
            remessa,
        )
        processo = _obter_ou_criar_processo_remessa(
            session_postgres,
            remessa_financeira,
            payload.processo_recebimento,
            usuario_atual.id,
        )
        agora = datetime.now(ZoneInfo('America/Sao_Paulo')).replace(
            tzinfo=None
        )
        for item, nota, valor_nfse in notas_validadas:
            nota_publica = _nota_publica(
                nota,
                {
                    'convenio': remessa['convenio'],
                    'cnpj_convenio': cnpj_remessa,
                },
            )
            conciliacao = ConciliacaoFaturamento(
                nfse_row_hash=nota.row_hash,
                numero_nfse=nota_publica['numero_nfse'],
                cnpj_convenio=cnpj_remessa,
                convenio=remessa['convenio'],
                valor_nfse=valor_nfse,
                impostos=nota_publica['impostos'],
                processo_recebimento=processo.processo_recebimento,
                data_previsao_recebimento=(
                    item.data_previsao_recebimento
                ),
                usuario_id=usuario_atual.id,
                data_recebimento=item.data_recebimento,
                conta_bancaria_id=item.conta_bancaria_id,
                conta_plano_contas=item.conta_plano_contas,
                conta_centro_custo=item.conta_centro_custo,
                lancamento_extrato_id=item.lancamento_extrato_id,
            )
            conciliacao.data_criacao = agora
            session_postgres.add(conciliacao)
            session_postgres.flush()
            valor_glosado = _money(item.valor_glosado)
            vinculo = ConciliacaoFaturamentoRemessa(
                conciliacao_id=conciliacao.id,
                cd_remessa=cd_remessa,
                convenio=remessa['convenio'],
                cnpj_convenio=cnpj_remessa,
                valor_total=(
                    _money(item.valor_alocado)
                    + _money(item.valor_impostos)
                    + valor_glosado
                ),
                sn_glosado='true' if item.sn_glosado else 'not',
                valor_glosado=valor_glosado,
                tp_conciliacao=tipo_conciliacao,
                processo_remessa_id=processo.id,
                valor_alocado_nfse=_money(item.valor_alocado),
                valor_impostos=_money(item.valor_impostos),
            )
            session_postgres.add(vinculo)
            session_postgres.flush()
            if item.sn_glosado:
                _registrar_itens_glosa_conciliacao(
                    session_postgres,
                    conciliacao,
                    vinculo,
                    itens_glosa[cd_remessa],
                )
            recebimento_criado = None
            if item.data_recebimento is not None:
                if item.conta_bancaria_id is None:
                    raise HTTPException(
                        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                        detail=(
                            'Selecione a conta bancaria para registrar o '
                            'recebimento da NFS-e.'
                        ),
                    )
                recebimento_criado, _ = _registrar_recebimento_remessa(
                    session=session_postgres,
                    remessa=remessa_financeira,
                    conciliacao_id=conciliacao.id,
                    numero_nfse=conciliacao.numero_nfse,
                    data_recebimento=item.data_recebimento,
                    valor_recebido=item.valor_alocado,
                    usuario_id=usuario_atual.id,
                    conta_bancaria_id=item.conta_bancaria_id,
                    conta_plano_contas=item.conta_plano_contas,
                    conta_centro_custo=item.conta_centro_custo,
                    lancamento_extrato_id=item.lancamento_extrato_id,
                )
            _registrar_auditoria_conciliacao(
                session_postgres,
                conciliacao.id,
                'criacao',
                usuario_atual.id,
                dados_novos=_snapshot_conciliacao(
                    conciliacao,
                    [vinculo],
                ),
            )
            if recebimento_criado is not None:
                _registrar_auditoria_conciliacao(
                    session_postgres,
                    conciliacao.id,
                    'recebimento',
                    usuario_atual.id,
                    dados_novos=_snapshot_conciliacao(
                        conciliacao,
                        [vinculo],
                        recebimento_criado,
                    ),
                )
        for lancamento in lancamentos:
            lancamento.conciliado = True
        session_postgres.commit()
        session_postgres.refresh(processo)
    except HTTPException:
        session_postgres.rollback()
        raise
    except IntegrityError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='A remessa ou uma das NFS-e ja possui este vinculo.',
        ) from exc

    resumo_atualizado = _resumos_remessas(
        session_postgres,
        {cd_remessa},
    ).get(cd_remessa, {})
    posicao_atualizada = _posicao_remessa(
        remessa,
        resumo_atualizado,
        _valores_acatados_por_remessa(
            session_postgres,
            {cd_remessa},
        ).get(cd_remessa, Decimal('0.00')),
        _recursos_abertos_por_remessa(
            session_postgres,
            {cd_remessa},
        ).get(cd_remessa, Decimal('0.00')),
    )
    return {
        'processo_remessa_id': processo.id,
        'cd_remessa': cd_remessa,
        'processo_recebimento': processo.processo_recebimento,
        'quantidade_notas': len(notas_validadas),
        'valor_alocado': _money(total_alocado),
        'valor_impostos': _money(total_impostos),
        'valor_glosado': _money(total_glosado),
        'valor_nao_conciliado': posicao_atualizada[
            'valor_nao_conciliado'
        ],
        'remessa': posicao_atualizada,
        'message': 'Remessa conciliada com as NFS-e informadas.',
    }


@router.get(
    '/conciliacao-faturamento/notas',
    status_code=HTTPStatus.OK,
    response_model=NfsesPendentesConciliacao,
)
def consultar_nfses_pendentes(  # noqa: PLR0913
    usuario_atual: ValidaUsuarioAtual,
    session: SessionPostgres,
    session_oracle: Session = Depends(get_session_oracle),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    try:
        convenios_por_cnpj = _consultar_convenios_hpc(session_oracle)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel consultar a HPC_V_CONVENIOS.',
        ) from exc

    query = _nota_pendente_query()
    if q:
        termo = f'%{q.strip()}%'
        termo_normalizado = q.strip().casefold()
        cnpjs_encontrados = [
            cnpj
            for cnpj, convenio in convenios_por_cnpj.items()
            if termo_normalizado in convenio['convenio'].casefold()
            or termo_normalizado in cnpj
        ]
        condicoes = [
            NfseXml.numero_nfse.ilike(termo),
            NfseXml.prestador_cnpj.ilike(termo),
            NfseXml.tomador_cnpj.ilike(termo),
        ]
        if cnpjs_encontrados:
            condicoes.append(
                or_(
                    func.regexp_replace(
                        NfseXml.prestador_cnpj,
                        '[^0-9]',
                        '',
                        'g',
                    ).in_(cnpjs_encontrados),
                    func.regexp_replace(
                        NfseXml.tomador_cnpj,
                        '[^0-9]',
                        '',
                        'g',
                    ).in_(cnpjs_encontrados),
                )
            )
        query = query.where(or_(*condicoes))
    query_notas_unicas = _nfses_unicas_query(query)
    notas_unicas = query_notas_unicas.subquery()
    total, valor_total_nfse = session.execute(
        select(
            func.count(),
            func.coalesce(
                func.sum(
                    cast(
                        func.nullif(
                            notas_unicas.c.valor_liquido_nfse,
                            '',
                        ),
                        Numeric(18, 2),
                    )
                ),
                0,
            ),
        ).select_from(notas_unicas)
    ).one()
    notas = session.scalars(
        query_notas_unicas
        .order_by(
            NfseXml.data_hora.desc(),
            NfseXml.numero_nfse.desc(),
        )
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        'notas': [
            _nota_publica(
                nota,
                _convenio_da_nfse(nota, convenios_por_cnpj),
            )
            for nota in notas
        ],
        'total': total,
        'valor_total_nfse': _money(valor_total_nfse),
        'limit': limit,
        'offset': offset,
    }


@router.get(
    '/conciliacao-faturamento/notas/{nfse_row_hash}/remessas',
    status_code=HTTPStatus.OK,
    response_model=RemessasConciliacaoList,
)
def consultar_remessas_para_nfse(  # noqa: PLR0913
    nfse_row_hash: str,
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
    session_oracle: Session = Depends(get_session_oracle),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
):
    nota = session_postgres.scalar(_nota_pendente_query(nfse_row_hash))
    if nota is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='NFS-e pendente de conciliacao nao encontrada.',
        )

    remessas_recebidas_integralmente = _remessas_conciliadas(
        session_postgres
    )
    remessas_previamente_conciliadas = _remessas_previamente_conciliadas(
        session_postgres
    )
    recursos_abertos = _recursos_abertos_por_remessa(session_postgres)
    valores_acatados = _valores_acatados_por_remessa(session_postgres)
    saldos_recebimento = _saldos_recebimento_por_remessa(
        session_postgres,
        valores_acatados=valores_acatados,
    )
    remessas_encerradas_por_acato = _remessas_encerradas_por_acato(
        session_postgres,
        valores_acatados,
        saldos_recebimento,
    )
    restricoes = _restricoes_nova_conciliacao(
        remessas_previamente_conciliadas,
        remessas_recebidas_integralmente,
        remessas_encerradas_por_acato,
        recursos_abertos,
        valores_acatados,
    )
    remessas_indisponiveis = set(restricoes)
    termo_pesquisa = (q or '').strip()
    if termo_pesquisa.isdigit():
        cd_remessa_pesquisada = int(termo_pesquisa)
        if cd_remessa_pesquisada in restricoes:
            message = restricoes[cd_remessa_pesquisada]
            return {
                'remessas': [],
                'message': message,
                'restricao': _restricao_remessa_publica(
                    cd_remessa_pesquisada,
                    message,
                    remessas_previamente_conciliadas,
                    remessas_recebidas_integralmente,
                    remessas_encerradas_por_acato,
                    saldos_recebimento,
                    valores_acatados,
                ),
            }
    try:
        convenio = _convenio_da_nfse(
            nota,
            _consultar_convenios_hpc(session_oracle),
        )
        if convenio is None:
            raise HTTPException(
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                detail='Convenio da NFS-e nao encontrado na HPC_V_CONVENIOS.',
            )
        remessas = _consultar_remessas_hpc(
            session_oracle,
            convenio['cnpj_convenio'],
            remessas_indisponiveis,
            q=q,
            limit=limit,
        )
    except SQLAlchemyError as exc:
        detail = (
            'Banco Oracle indisponivel no momento.'
            if _is_oracle_connect_timeout(exc)
            else 'Erro ao consultar remessas na HPC_V_CONTA_ATENDIMENTO.'
        )
        raise HTTPException(
            status_code=(
                HTTPStatus.SERVICE_UNAVAILABLE
                if _is_oracle_connect_timeout(exc)
                else HTTPStatus.INTERNAL_SERVER_ERROR
            ),
            detail=detail,
        ) from exc

    _enriquecer_remessas_com_recurso(
        remessas,
        recursos_abertos,
        saldos_recebimento,
        valores_acatados,
    )

    return {'remessas': remessas}


@router.get(
    '/contas-bancarias',
    status_code=HTTPStatus.OK,
    response_model=ContasBancariasRecebimentoList,
)
def consultar_contas_bancarias(
    usuario_atual: ValidaUsuarioAtual,
    session_oracle: Session = Depends(get_session_oracle),
):
    try:
        contas = session_oracle.scalars(
            select(ModelHpcContaBancaria).order_by(
                ModelHpcContaBancaria.ds_con_cor,
                ModelHpcContaBancaria.cd_agencia,
                ModelHpcContaBancaria.nr_conta,
            )
        ).all()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel consultar a HPC_V_CONTAS_BANCARIAS.',
        ) from exc
    return {
        'contas': [
            {
                'id': int(conta.cd_con_cor),
                'banco': conta.ds_con_cor,
                'descricao': conta.ds_con_cor,
                'agencia': conta.cd_agencia,
                'digito_agencia': conta.cd_digito_agencia,
                'conta': conta.nr_conta,
                'digito': conta.cd_digito_conta_corrente,
            }
            for conta in contas
        ]
    }


@router.get(
    '/lancamentos-extrato',
    status_code=HTTPStatus.OK,
    response_model=LancamentosExtratoBancarioList,
)
def consultar_lancamentos_extrato(  # noqa: PLR0913
    usuario_atual: ValidaUsuarioAtual,
    session: SessionPostgres,
    conta_bancaria_id: int = Query(gt=0),
    data_recebimento: date | None = Query(default=None),
    incluir_lancamento_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=100, ge=1, le=500),
):
    query = (
        select(LancamentoExtratoBancario)
        .where(
            LancamentoExtratoBancario.conta_bancaria_id == conta_bancaria_id,
            or_(
                LancamentoExtratoBancario.conciliado.is_(False),
                LancamentoExtratoBancario.id == incluir_lancamento_id,
            ),
        )
        .order_by(
            LancamentoExtratoBancario.data_lancamento.desc(),
            LancamentoExtratoBancario.id.desc(),
        )
        .limit(limit)
    )
    if data_recebimento is not None:
        query = query.where(
            LancamentoExtratoBancario.data_lancamento == data_recebimento
        )
    return {'lancamentos': session.scalars(query).all()}


@router.get(
    '/conciliacao-faturamento/recebimentos-remessas',
    status_code=HTTPStatus.OK,
    response_model=RecebimentosRemessaList,
)
def consultar_recebimentos_remessas(  # noqa: PLR0913
    usuario_atual: ValidaUsuarioAtual,
    session: SessionPostgres,
    cd_remessa: int | None = Query(default=None, gt=0),
    numero_nfse: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    filters = []
    if cd_remessa is not None:
        filters.append(RecebimentoRemessa.cd_remessa == cd_remessa)
    if numero_nfse is not None and numero_nfse.strip():
        filters.append(
            RecebimentoRemessa.numero_nfse == numero_nfse.strip()
        )

    query = select(RecebimentoRemessa)
    count_query = select(func.count(RecebimentoRemessa.id))
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    recebimentos = session.scalars(
        query.order_by(
            RecebimentoRemessa.data_recebimento.desc(),
            RecebimentoRemessa.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    ).all()
    ids_remessa = {item.cd_remessa for item in recebimentos}
    remessas = {
        item.cd_remessa: item
        for item in session.scalars(
            select(RemessaFinanceira).where(
                RemessaFinanceira.cd_remessa.in_(ids_remessa)
            )
        ).all()
    }
    totais = {
        int(row.cd_remessa): _money(row.valor_total_recebido)
        for row in session.execute(
            select(
                RecebimentoRemessa.cd_remessa,
                func.sum(RecebimentoRemessa.valor_recebido).label(
                    'valor_total_recebido'
                ),
            )
            .where(RecebimentoRemessa.cd_remessa.in_(ids_remessa))
            .group_by(RecebimentoRemessa.cd_remessa)
        ).all()
    }
    valores_acatados = _valores_acatados_por_remessa(session, ids_remessa)
    return {
        'recebimentos': [
            _recebimento_remessa_publico(
                item,
                remessas[item.cd_remessa],
                totais[item.cd_remessa],
                valores_acatados.get(item.cd_remessa, Decimal('0.00')),
            )
            for item in recebimentos
        ],
        'total': session.scalar(count_query) or 0,
        'limit': limit,
        'offset': offset,
    }


def _item_follow_up_glosa(registros: list[RegistroGlosa]) -> dict:
    registro_recusa = next(
        (
            registro
            for registro in reversed(registros)
            if registro.status_tratativa == 'recurso'
        ),
        None,
    )
    registro_acato = next(
        (
            registro
            for registro in reversed(registros)
            if registro.status_tratativa == 'acato'
        ),
        None,
    )
    registro_pendente = next(
        (
            registro
            for registro in registros
            if registro.status_tratativa == 'pendente'
        ),
        None,
    )
    registro = registro_pendente or registro_recusa or registro_acato
    if registro is None:  # pragma: no cover - protegido pelo agrupamento
        raise ValueError('Item de Follow-Up sem registro de glosa.')
    return {
        'cd_paciente': registro.codigo_paciente,
        'nm_paciente': registro.nm_paciente,
        'cd_remessa': registro.cd_remessa,
        'cd_atendimento': registro.cd_atendimento,
        'cd_reg': registro.conta,
        'cd_lancamento': registro.cd_lancamento,
        'cd_prestador': registro.cd_prestador,
        'nm_prestador': registro.prestador,
        'cd_convenio': registro.cd_convenio,
        'nm_convenio': registro.convenio,
        'tp_atendimento': registro.tp_atendimento,
        'cd_pro_fat': registro.procedimento,
        'cd_gru_pro': registro.cd_gru_pro,
        'ds_gru_pro': registro.ds_gru_pro,
        'cd_gru_fat': registro.cd_gru_fat,
        'ds_gru_fat': registro.ds_gru_fat,
        'descricao': registro.descricao_item or registro.descricao_glosa,
        'nr_guia': registro.guia,
        'dt_atendimento': registro.data_atendimento,
        'dt_alta': registro.data_alta,
        'dt_lancamento': registro.data_lancamento,
        'qt_lancamento': registro.qtd_registro or Decimal('1.00'),
        'vl_total_conta': registro.valor,
        'registro_glosa': registro,
        'registro_recusa': registro_recusa,
        'registro_acato': registro_acato,
    }


def _pacientes_follow_up_glosa(registros: list[RegistroGlosa]) -> list[dict]:
    pacientes: dict[tuple[int, str], dict] = {}
    for registro in registros:
        nome = registro.nm_paciente or f'Paciente {registro.codigo_paciente}'
        chave = (registro.codigo_paciente, nome)
        if chave not in pacientes:
            pacientes[chave] = {
                'codigo_paciente': registro.codigo_paciente,
                'nm_paciente': nome,
                'itens_por_chave': {},
            }
        chave_item = (
            registro.cd_remessa,
            registro.cd_atendimento,
            registro.conta,
            registro.cd_lancamento,
        )
        pacientes[chave]['itens_por_chave'].setdefault(
            chave_item,
            [],
        ).append(registro)

    resultado = []
    for paciente in pacientes.values():
        resultado.append(
            {
                'codigo_paciente': paciente['codigo_paciente'],
                'nm_paciente': paciente['nm_paciente'],
                'itens': [
                    _item_follow_up_glosa(registros_item)
                    for registros_item in paciente['itens_por_chave'].values()
                ],
            }
        )
    return resultado


def _sincronizar_itens_follow_up(  # noqa: PLR0912
    session_postgres: Session,
    session_oracle: Session,
) -> int:
    possui_registro = (
        select(RegistroGlosa.id)
        .where(
            RegistroGlosa.conciliacao_remessa_id
            == ConciliacaoFaturamentoRemessa.id
        )
        .exists()
    )
    registro_sem_grupo = (
        select(RegistroGlosa.id)
        .where(
            RegistroGlosa.conciliacao_remessa_id
            == ConciliacaoFaturamentoRemessa.id,
            or_(
                RegistroGlosa.cd_gru_pro.is_(None),
                RegistroGlosa.ds_gru_pro.is_(None),
                RegistroGlosa.cd_gru_fat.is_(None),
                RegistroGlosa.ds_gru_fat.is_(None),
            ),
        )
        .exists()
    )
    rows = session_postgres.execute(
        select(
            ConciliacaoFaturamentoRemessa,
            ConciliacaoFaturamento,
        )
        .join(
            ConciliacaoFaturamento,
            ConciliacaoFaturamento.id
            == ConciliacaoFaturamentoRemessa.conciliacao_id,
        )
        .where(
            ConciliacaoFaturamento.ativo.is_(True),
            ConciliacaoFaturamentoRemessa.sn_glosado == 'true',
            ConciliacaoFaturamentoRemessa.valor_glosado > 0,
            or_(~possui_registro, registro_sem_grupo),
        )
        .order_by(ConciliacaoFaturamentoRemessa.id)
        .with_for_update()
    ).all()
    if not rows:
        return 0

    vinculos_sincronizar = []
    for vinculo, conciliacao in rows:
        registros = session_postgres.scalars(
            select(RegistroGlosa).where(
                RegistroGlosa.conciliacao_remessa_id == vinculo.id
            )
        ).all()
        if not registros or any(
            registro.cd_gru_pro is None
            or registro.ds_gru_pro is None
            or registro.cd_gru_fat is None
            or registro.ds_gru_fat is None
            for registro in registros
        ):
            vinculos_sincronizar.append((vinculo, conciliacao, registros))
    if not vinculos_sincronizar:
        return 0

    por_cnpj: dict[
        str,
        list[
            tuple[
                ConciliacaoFaturamentoRemessa,
                ConciliacaoFaturamento,
                list[RegistroGlosa],
            ]
        ],
    ] = {}
    for vinculo, conciliacao, registros in vinculos_sincronizar:
        por_cnpj.setdefault(vinculo.cnpj_convenio, []).append(
            (vinculo, conciliacao, registros)
        )

    itens_por_vinculo: dict[int, list[dict]] = {}
    for cnpj_convenio, conciliacoes in por_cnpj.items():
        ids_remessas = {
            vinculo.cd_remessa for vinculo, _, _ in conciliacoes
        }
        itens_por_remessa = _carregar_itens_glosa_conciliacao(
            session_oracle,
            cnpj_convenio,
            ids_remessas,
        )
        for vinculo, _, _ in conciliacoes:
            itens_por_vinculo[vinculo.id] = itens_por_remessa[
                vinculo.cd_remessa
            ]

    total_alteracoes = 0
    for vinculo, conciliacao, registros in vinculos_sincronizar:
        itens = itens_por_vinculo[vinculo.id]
        if not registros:
            _registrar_itens_glosa_conciliacao(
                session_postgres,
                conciliacao,
                vinculo,
                itens,
            )
            total_alteracoes += len(itens)
            continue

        itens_por_chave = {
            (item['conta'], item['cd_lancamento']): item for item in itens
        }
        for registro in registros:
            item = itens_por_chave.get(
                (registro.conta, registro.cd_lancamento)
            )
            registro.cd_gru_pro = item['cd_gru_pro'] if item else 0
            registro.ds_gru_pro = (
                item['ds_gru_pro'] if item else 'Grupo nao informado'
            )
            registro.cd_gru_fat = item['cd_gru_fat'] if item else 0
            registro.ds_gru_fat = (
                item['ds_gru_fat'] if item else 'Grupo nao informado'
            )
            if item and not registro.descricao_item:
                registro.descricao_item = item['descricao_item']
            if item and registro.data_alta is None:
                registro.data_alta = item['data_alta']
            if item and registro.data_lancamento is None:
                registro.data_lancamento = item['data_lancamento']
            total_alteracoes += 1
    session_postgres.commit()
    return total_alteracoes


@router.get(
    '/conciliacao-faturamento/glosas-pendentes',
    status_code=HTTPStatus.OK,
    response_model=FollowUpGlosasList,
)
def consultar_follow_up_glosas(  # noqa: PLR0913
    usuario_atual: ValidaUsuarioAtual,
    session: SessionPostgres,
    session_oracle: Session = Depends(get_session_oracle),
    q: str | None = Query(default=None, max_length=100),
    numero_nfse: Annotated[str | None, Query(max_length=100)] = None,
    cd_remessa: Annotated[int | None, Query(ge=1)] = None,
    convenio: Annotated[str | None, Query(max_length=100)] = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    conciliacao_remessa_id: Annotated[int | None, Query(ge=1)] = None,
    incluir_detalhes: bool = True,
):
    _sincronizar_itens_follow_up(session, session_oracle)
    valores_alocados = (
        select(
            RegistroGlosa.conciliacao_remessa_id.label(
                'conciliacao_remessa_id'
            ),
            func.sum(RegistroGlosa.valor_recursado).label('valor_alocado'),
        )
        .where(
            RegistroGlosa.conciliacao_remessa_id.is_not(None),
            RegistroGlosa.sn_ativo == 'true',
            RegistroGlosa.valor_recursado.is_not(None),
        )
        .group_by(RegistroGlosa.conciliacao_remessa_id)
        .subquery()
    )
    data_entrega = (
        select(func.min(RegistroGlosa.data_glosa))
        .where(
            RegistroGlosa.conciliacao_remessa_id
            == ConciliacaoFaturamentoRemessa.id
        )
        .correlate(ConciliacaoFaturamentoRemessa)
        .scalar_subquery()
    )
    valor_pendente = (
        ConciliacaoFaturamentoRemessa.valor_glosado
        - func.coalesce(valores_alocados.c.valor_alocado, 0)
    )
    valor_tratado = func.coalesce(valores_alocados.c.valor_alocado, 0)
    filtros = [
        ConciliacaoFaturamento.ativo.is_(True),
        ConciliacaoFaturamentoRemessa.sn_glosado == 'true',
        ConciliacaoFaturamentoRemessa.valor_glosado > 0,
        valor_pendente > 0,
    ]
    if conciliacao_remessa_id is not None:
        filtros.append(
            ConciliacaoFaturamentoRemessa.id == conciliacao_remessa_id
        )
    termo = (q or '').strip()
    if termo:
        pattern = f'%{termo}%'
        filtros.append(
            or_(
                cast(
                    ConciliacaoFaturamentoRemessa.cd_remessa,
                    String,
                ).ilike(pattern),
                ConciliacaoFaturamentoRemessa.convenio.ilike(pattern),
                ConciliacaoFaturamento.numero_nfse.ilike(pattern),
            )
        )
    termo_nfse = (numero_nfse or '').strip()
    if termo_nfse:
        filtros.append(
            ConciliacaoFaturamento.numero_nfse.ilike(f'%{termo_nfse}%')
        )
    if cd_remessa is not None:
        filtros.append(
            ConciliacaoFaturamentoRemessa.cd_remessa == cd_remessa
        )
    termo_convenio = (convenio or '').strip()
    if termo_convenio:
        filtros.append(
            ConciliacaoFaturamentoRemessa.convenio.ilike(
                f'%{termo_convenio}%'
            )
        )

    consulta_base = (
        select(
            ConciliacaoFaturamentoRemessa,
            ConciliacaoFaturamento,
            data_entrega.label('data_entrega'),
            valor_pendente.label('valor_pendente'),
            valor_tratado.label('valor_tratado'),
        )
        .join(
            ConciliacaoFaturamento,
            ConciliacaoFaturamento.id
            == ConciliacaoFaturamentoRemessa.conciliacao_id,
        )
        .outerjoin(
            valores_alocados,
            valores_alocados.c.conciliacao_remessa_id
            == ConciliacaoFaturamentoRemessa.id,
        )
        .where(*filtros)
    )
    (
        total,
        valor_total_glosado,
        valor_total_pendente,
        valor_total_tratado,
    ) = session.execute(
        select(
            func.count(),
            func.coalesce(
                func.sum(ConciliacaoFaturamentoRemessa.valor_glosado),
                0,
            ),
            func.coalesce(func.sum(valor_pendente), 0),
            func.coalesce(func.sum(valor_tratado), 0),
        )
        .select_from(ConciliacaoFaturamentoRemessa)
        .join(
            ConciliacaoFaturamento,
            ConciliacaoFaturamento.id
            == ConciliacaoFaturamentoRemessa.conciliacao_id,
        )
        .outerjoin(
            valores_alocados,
            valores_alocados.c.conciliacao_remessa_id
            == ConciliacaoFaturamentoRemessa.id,
        )
        .where(*filtros)
    ).one()
    rows = session.execute(
        consulta_base
        .order_by(data_entrega, ConciliacaoFaturamentoRemessa.id)
        .offset(offset)
        .limit(limit)
    ).all()
    codigos_remessa = {row[0].cd_remessa for row in rows}
    totais_remessas_hpc = {}
    try:
        totais_remessas_hpc = sincronizar_totais_remessas_financeiras(
            session,
            session_oracle,
            codigos_remessa,
        )
    except SQLAlchemyError:
        # O snapshot persistido continua disponível se o Oracle oscilar.
        pass
    remessas_financeiras = {
        remessa.cd_remessa: remessa
        for remessa in session.scalars(
            select(RemessaFinanceira).where(
                RemessaFinanceira.cd_remessa.in_(codigos_remessa)
            )
        )
    }
    ids_vinculos = {row[0].id for row in rows}
    registros_por_vinculo: dict[int, list[RegistroGlosa]] = {
        vinculo_id: [] for vinculo_id in ids_vinculos
    }
    if incluir_detalhes and ids_vinculos:
        registros = session.scalars(
            select(RegistroGlosa)
            .where(
                RegistroGlosa.conciliacao_remessa_id.in_(ids_vinculos),
                RegistroGlosa.sn_ativo == 'true',
            )
            .order_by(
                RegistroGlosa.conciliacao_remessa_id,
                RegistroGlosa.nm_paciente,
                RegistroGlosa.cd_atendimento,
                RegistroGlosa.conta,
                RegistroGlosa.cd_lancamento,
            )
        ).all()
        for registro in registros:
            registros_por_vinculo[registro.conciliacao_remessa_id].append(
                registro
            )

    cards = []
    for vinculo, conciliacao, entrega, pendente, tratado in rows:
        remessa_financeira = remessas_financeiras.get(vinculo.cd_remessa)
        cards.append(
            {
                'conciliacao_remessa_id': vinculo.id,
                'cd_remessa': vinculo.cd_remessa,
                'convenio': vinculo.convenio,
                'data_entrega': entrega or conciliacao.data_criacao.date(),
                'numero_nfse': conciliacao.numero_nfse,
                'valor_remessa': _money(
                    totais_remessas_hpc.get(
                        vinculo.cd_remessa,
                        (
                            remessa_financeira.valor_total
                            if remessa_financeira is not None
                            else vinculo.valor_total
                        ),
                    )
                ),
                'valor_glosado': _money(vinculo.valor_glosado),
                'valor_glosa_pendente': max(
                    _money(pendente),
                    Decimal('0.00'),
                ),
                'valor_total_tratado': _money(tratado),
                'pacientes': (
                    _pacientes_follow_up_glosa(
                        registros_por_vinculo[vinculo.id]
                    )
                    if incluir_detalhes
                    else []
                ),
            }
        )
    return {
        'cards': cards,
        'total': int(total),
        'valor_total_glosado': _money(valor_total_glosado),
        'valor_total_pendente': _money(valor_total_pendente),
        'valor_total_tratado': _money(valor_total_tratado),
        'limit': limit,
        'offset': offset,
    }


def _conciliacao_alteracao_publica(
    conciliacao: ConciliacaoFaturamento,
    usuario_id: int,
    data_operacao: datetime,
    message: str,
) -> dict:
    return {
        'id': conciliacao.id,
        'ativo': conciliacao.ativo,
        'processo_recebimento': conciliacao.processo_recebimento,
        'data_previsao_recebimento': (
            conciliacao.data_previsao_recebimento
        ),
        'usuario_operacao_id': usuario_id,
        'data_operacao': data_operacao,
        'message': message,
    }


def _atualizar_valores_conciliacao(  # noqa: PLR0912, PLR0915
    session: Session,
    session_oracle: Session,
    conciliacao: ConciliacaoFaturamento,
    vinculos: list[ConciliacaoFaturamentoRemessa],
    payload: ConciliacaoFaturamentoUpdate,
) -> None:
    if not payload.remessas:
        return
    vinculos_por_remessa = {item.cd_remessa: item for item in vinculos}
    ajustes = {item.cd_remessa: item for item in payload.remessas}
    codigos_invalidos = sorted(ajustes.keys() - vinculos_por_remessa.keys())
    if codigos_invalidos:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                'As remessas informadas nao pertencem a conciliacao: '
                + ', '.join(str(item) for item in codigos_invalidos)
                + '.'
            ),
        )

    valor_atual_nfse = sum(
        (_valor_alocado_vinculo(item) for item in vinculos),
        Decimal('0.00'),
    )
    valor_novo_nfse = sum(
        (
            _money(ajustes[item.cd_remessa].valor_recebido)
            if item.cd_remessa in ajustes
            else _valor_alocado_vinculo(item)
            for item in vinculos
        ),
        Decimal('0.00'),
    )
    chave_nfse = (
        str(conciliacao.numero_nfse),
        _normalize_cnpj(conciliacao.cnpj_convenio),
    )
    valor_utilizado_nfse = _valores_utilizados_nfse(session).get(
        chave_nfse,
        Decimal('0.00'),
    )
    saldo_editavel_nfse = max(
        _money(conciliacao.valor_nfse)
        - max(valor_utilizado_nfse - valor_atual_nfse, Decimal('0.00')),
        Decimal('0.00'),
    )
    if valor_novo_nfse > saldo_editavel_nfse:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                'A soma dos valores recebidos excede o saldo disponivel '
                f'da NFS-e ({_valor_reais_mensagem(saldo_editavel_nfse)}).'
            ),
        )

    valor_atual_impostos = sum(
        (_valor_impostos_vinculo(item) for item in vinculos),
        Decimal('0.00'),
    )
    valor_novo_impostos = sum(
        (
            _money(ajustes[item.cd_remessa].valor_impostos)
            if item.cd_remessa in ajustes
            else _valor_impostos_vinculo(item)
            for item in vinculos
        ),
        Decimal('0.00'),
    )
    valor_utilizado_impostos = _valores_impostos_utilizados_nfse(
        session
    ).get(chave_nfse, Decimal('0.00'))
    saldo_editavel_impostos = max(
        _money(conciliacao.impostos)
        - max(
            valor_utilizado_impostos - valor_atual_impostos,
            Decimal('0.00'),
        ),
        Decimal('0.00'),
    )
    if valor_novo_impostos > saldo_editavel_impostos:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                'A soma das retencoes informadas excede o saldo de retencoes '
                f'da NFS-e '
                f'({_valor_reais_mensagem(saldo_editavel_impostos)}).'
            ),
        )

    codigos = set(ajustes)
    resumos = _resumos_remessas(session, codigos)
    valores_acatados = _valores_acatados_por_remessa(session, codigos)
    recursos_abertos = _recursos_abertos_por_remessa(session, codigos)
    registros_por_vinculo: dict[int, list[RegistroGlosa]] = {
        item.id: [] for item in vinculos if item.cd_remessa in ajustes
    }
    if registros_por_vinculo:
        for registro in session.scalars(
            select(RegistroGlosa).where(
                RegistroGlosa.conciliacao_remessa_id.in_(
                    registros_por_vinculo
                )
            )
        ):
            registros_por_vinculo[registro.conciliacao_remessa_id].append(
                registro
            )

    codigos_carregar_itens = set()
    for cd_remessa, ajuste in ajustes.items():
        vinculo = vinculos_por_remessa[cd_remessa]
        valor_recebido = _money(ajuste.valor_recebido)
        valor_impostos = _money(ajuste.valor_impostos)
        valor_glosado = _money(ajuste.valor_glosado)
        valor_atual = (
            _valor_conciliado_vinculo(vinculo)
            + _money(vinculo.valor_glosado)
        )
        remessa = session.get(RemessaFinanceira, cd_remessa)
        if remessa is None:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail=(
                    f'A remessa {cd_remessa} nao possui controle financeiro.'
                ),
            )
        posicao = _posicao_remessa(
            {
                'cd_remessa': cd_remessa,
                'convenio': remessa.convenio,
                'cnpj_convenio': remessa.cnpj_convenio,
                'valor_total': remessa.valor_total,
                'data_competencia': remessa.data_competencia,
            },
            resumos.get(cd_remessa),
            valores_acatados.get(cd_remessa, Decimal('0.00')),
            recursos_abertos.get(cd_remessa, Decimal('0.00')),
        )
        limite = valor_atual + _money(
            posicao['valor_disponivel_conciliacao']
        )
        if valor_recebido + valor_impostos + valor_glosado > limite:
            raise HTTPException(
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                detail=(
                    f'A soma do valor recebido, das retencoes e da glosa da '
                    f'remessa '
                    f'{cd_remessa} excede o saldo disponivel '
                    f'({_valor_reais_mensagem(limite)}).'
                ),
            )
        if valor_glosado != _money(vinculo.valor_glosado):
            registros = registros_por_vinculo[vinculo.id]
            if any(
                registro.processo_recurso
                or registro.dt_recurso is not None
                or registro.valor_recursado is not None
                or registro.sn_glosado == 'not'
                or registro.valor_recebido is not None
                for registro in registros
            ):
                raise HTTPException(
                    status_code=HTTPStatus.CONFLICT,
                    detail=(
                        f'A glosa da remessa {cd_remessa} ja possui '
                        'tratamento e nao pode ser alterada.'
                    ),
                )
            if valor_glosado > 0 and not registros:
                codigos_carregar_itens.add(cd_remessa)

    itens_por_remessa = _carregar_itens_glosa_conciliacao(
        session_oracle,
        conciliacao.cnpj_convenio,
        codigos_carregar_itens,
    )
    for cd_remessa, ajuste in ajustes.items():
        vinculo = vinculos_por_remessa[cd_remessa]
        valor_recebido = _money(ajuste.valor_recebido)
        valor_impostos = _money(ajuste.valor_impostos)
        valor_glosado = _money(ajuste.valor_glosado)
        valor_glosado_anterior = _money(vinculo.valor_glosado)
        vinculo.valor_alocado_nfse = valor_recebido
        vinculo.valor_impostos = valor_impostos
        vinculo.valor_glosado = valor_glosado
        vinculo.valor_total = (
            valor_recebido + valor_impostos + valor_glosado
        )
        vinculo.sn_glosado = 'true' if valor_glosado > 0 else 'not'
        registros = registros_por_vinculo[vinculo.id]
        glosa_alterada = valor_glosado != valor_glosado_anterior
        if glosa_alterada and valor_glosado <= 0:
            for registro in registros:
                registro.sn_ativo = 'not'
        elif glosa_alterada and registros:
            for registro in registros:
                registro.sn_ativo = 'true'
                registro.sn_glosado = 'true'
                registro.processo_controle_fatura_gab = (
                    payload.processo_recebimento
                    or conciliacao.processo_recebimento
                )
        elif glosa_alterada:
            _registrar_itens_glosa_conciliacao(
                session,
                conciliacao,
                vinculo,
                itens_por_remessa[cd_remessa],
            )
        elif payload.processo_recebimento is not None:
            for registro in registros:
                registro.processo_controle_fatura_gab = (
                    payload.processo_recebimento
                )


def _auditorias_linha_tempo_conciliacoes(
    session: Session,
    conciliacoes: list[ConciliacaoFaturamento],
    vinculos_por_conciliacao: dict[
        int, list[ConciliacaoFaturamentoRemessa]
    ],
) -> tuple[
    list[AuditoriaConciliacaoFaturamento],
    dict[int, list[AuditoriaConciliacaoFaturamento]],
]:
    if not conciliacoes:
        return [], {}
    ids = {conciliacao.id for conciliacao in conciliacoes}
    hashes_por_conciliacao = {
        conciliacao.id: conciliacao.nfse_row_hash
        for conciliacao in conciliacoes
    }
    codigos_por_conciliacao = {
        conciliacao_id: {
            vinculo.cd_remessa
            for vinculo in vinculos_por_conciliacao[conciliacao_id]
        }
        for conciliacao_id in ids
    }
    hashes = set(hashes_por_conciliacao.values())
    codigos = {
        codigo
        for codigos_conciliacao in codigos_por_conciliacao.values()
        for codigo in codigos_conciliacao
    }
    ids_por_vinculo = {}
    if hashes and codigos:
        for nfse_row_hash, cd_remessa, conciliacao_id in session.execute(
            select(
                ConciliacaoFaturamento.nfse_row_hash,
                ConciliacaoFaturamentoRemessa.cd_remessa,
                ConciliacaoFaturamento.id,
            )
            .join(
                ConciliacaoFaturamentoRemessa,
                ConciliacaoFaturamentoRemessa.conciliacao_id
                == ConciliacaoFaturamento.id,
            )
            .where(
                ConciliacaoFaturamento.nfse_row_hash.in_(hashes),
                ConciliacaoFaturamentoRemessa.cd_remessa.in_(codigos),
            )
        ):
            ids_por_vinculo.setdefault((nfse_row_hash, cd_remessa), set()).add(
                conciliacao_id
            )
    ids_historico_por_conciliacao = {
        conciliacao_id: {conciliacao_id} for conciliacao_id in ids
    }
    for conciliacao_id in ids:
        nfse_row_hash = hashes_por_conciliacao[conciliacao_id]
        for cd_remessa in codigos_por_conciliacao[conciliacao_id]:
            ids_historico_por_conciliacao[conciliacao_id].update(
                ids_por_vinculo.get((nfse_row_hash, cd_remessa), set())
            )

    ids_auditoria = {
        historico_id
        for ids_historico in ids_historico_por_conciliacao.values()
        for historico_id in ids_historico
    }
    auditorias = list(
        session.scalars(
            select(AuditoriaConciliacaoFaturamento)
            .where(
                AuditoriaConciliacaoFaturamento.conciliacao_id.in_(
                    ids_auditoria
                )
            )
            .order_by(
                AuditoriaConciliacaoFaturamento.data_operacao.desc(),
                AuditoriaConciliacaoFaturamento.id.desc(),
            )
        )
    )
    return auditorias, {
        conciliacao_id: [
            auditoria
            for auditoria in auditorias
            if auditoria.conciliacao_id in ids_historico
        ]
        for conciliacao_id, ids_historico in (
            ids_historico_por_conciliacao.items()
        )
    }


def _filtros_pesquisa_conciliacoes(
    q: str | None,
    numero_nfse: str | None,
    cd_remessa: str | None,
    convenio: str | None,
    processo_recebimento: str | None,
) -> list:
    filtros = []
    termo = (q or '').strip()
    if termo:
        pattern = f'%{termo}%'
        filtros.append(
            or_(
                ConciliacaoFaturamento.numero_nfse.ilike(pattern),
                ConciliacaoFaturamento.convenio.ilike(pattern),
                ConciliacaoFaturamento.cnpj_convenio.ilike(pattern),
                ConciliacaoFaturamento.processo_recebimento.ilike(pattern),
                cast(
                    ConciliacaoFaturamentoRemessa.cd_remessa,
                    String,
                ).ilike(pattern),
            )
        )
    for valor, coluna in (
        (numero_nfse, ConciliacaoFaturamento.numero_nfse),
        (convenio, ConciliacaoFaturamento.convenio),
        (
            processo_recebimento,
            ConciliacaoFaturamento.processo_recebimento,
        ),
    ):
        valor_normalizado = (valor or '').strip()
        if valor_normalizado:
            filtros.append(coluna.ilike(f'%{valor_normalizado}%'))
    remessa_normalizada = (cd_remessa or '').strip()
    if remessa_normalizada:
        filtros.append(
            cast(ConciliacaoFaturamentoRemessa.cd_remessa, String).ilike(
                f'%{remessa_normalizada}%'
            )
        )
    return filtros


@router.get(
    '/conciliacao-faturamento/conciliacoes',
    status_code=HTTPStatus.OK,
    response_model=ConciliacoesGerenciamentoList,
)
def consultar_conciliacoes_faturamento(  # noqa: PLR0913, PLR0915
    usuario_atual: ValidaUsuarioAtual,
    session: SessionPostgres,
    session_oracle: Session = Depends(get_session_oracle),
    q: str | None = Query(default=None, max_length=100),
    numero_nfse: Annotated[str | None, Query(max_length=100)] = None,
    remessa_filtro: Annotated[
        str | None, Query(alias='cd_remessa', max_length=30)
    ] = None,
    convenio: Annotated[str | None, Query(max_length=100)] = None,
    processo_recebimento: Annotated[
        str | None, Query(max_length=100)
    ] = None,
    situacao: str | None = Query(default=None, max_length=30),
    incluir_inativas: bool = Query(default=False),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    del usuario_atual
    recebimento_existe = (
        select(RecebimentoRemessa.id)
        .where(
            RecebimentoRemessa.conciliacao_id
            == ConciliacaoFaturamentoRemessa.conciliacao_id,
            RecebimentoRemessa.cd_remessa
            == ConciliacaoFaturamentoRemessa.cd_remessa,
        )
        .exists()
    )
    matching_filters = _filtros_pesquisa_conciliacoes(
        q,
        numero_nfse,
        remessa_filtro,
        convenio,
        processo_recebimento,
    )
    if not incluir_inativas:
        matching_filters.append(ConciliacaoFaturamento.ativo.is_(True))
    remessas_correspondentes = (
        select(ConciliacaoFaturamentoRemessa.cd_remessa)
        .join(
            ConciliacaoFaturamento,
            ConciliacaoFaturamento.id
            == ConciliacaoFaturamentoRemessa.conciliacao_id,
        )
        .where(*matching_filters)
        .distinct()
    )
    detail_filters = [
        ConciliacaoFaturamentoRemessa.cd_remessa.in_(
            remessas_correspondentes
        )
    ]
    if not incluir_inativas:
        detail_filters.append(ConciliacaoFaturamento.ativo.is_(True))
    remessas_agrupadas = (
        select(
            ConciliacaoFaturamentoRemessa.cd_remessa.label('cd_remessa'),
            func.max(ConciliacaoFaturamento.data_criacao).label(
                'ultima_conciliacao'
            ),
            func.max(
                case(
                    (ConciliacaoFaturamento.ativo.is_(True), 1),
                    else_=0,
                )
            ).label('ativa'),
            func.max(case((recebimento_existe, 1), else_=0)).label(
                'recebida'
            ),
        )
        .join(
            ConciliacaoFaturamento,
            ConciliacaoFaturamento.id
            == ConciliacaoFaturamentoRemessa.conciliacao_id,
        )
        .where(*detail_filters)
        .group_by(ConciliacaoFaturamentoRemessa.cd_remessa)
        .subquery()
    )
    remessas_filtradas = select(remessas_agrupadas)
    if situacao == 'recebido':
        remessas_filtradas = remessas_filtradas.where(
            remessas_agrupadas.c.recebida == 1
        )
    elif situacao == 'sem_recebimento':
        remessas_filtradas = remessas_filtradas.where(
            remessas_agrupadas.c.recebida == 0
        )
    remessas_filtradas = remessas_filtradas.subquery()
    summary = session.execute(
        select(
            func.count(remessas_filtradas.c.cd_remessa),
            func.sum(remessas_filtradas.c.ativa),
            func.sum(case((remessas_filtradas.c.ativa == 0, 1), else_=0)),
            func.sum(remessas_filtradas.c.recebida),
            func.sum(
                case((remessas_filtradas.c.recebida == 0, 1), else_=0)
            )
        ).select_from(remessas_filtradas)
    ).one()
    total, total_ativas, total_inativas, total_recebidas, total_pendentes = (
        int(value or 0) for value in summary
    )
    codigos_remessa = list(
        session.scalars(
            select(remessas_filtradas.c.cd_remessa)
            .order_by(
                remessas_filtradas.c.ultima_conciliacao.desc(),
                remessas_filtradas.c.cd_remessa.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
    )
    if not codigos_remessa:
        return {
            'conciliacoes': [],
            'total': total,
            'total_ativas': total_ativas,
            'total_inativas': total_inativas,
            'total_recebidas': total_recebidas,
            'total_sem_recebimento': total_pendentes,
            'limit': limit,
            'offset': offset,
        }
    totais_remessas_hpc = {}
    try:
        totais_remessas_hpc = sincronizar_totais_remessas_financeiras(
            session,
            session_oracle,
            set(codigos_remessa),
        )
    except SQLAlchemyError:
        # Mantém a última posição persistida quando o Oracle está indisponível.
        pass

    rows = session.execute(
        select(
            ConciliacaoFaturamentoRemessa,
            ConciliacaoFaturamento,
        )
        .join(
            ConciliacaoFaturamento,
            ConciliacaoFaturamento.id
            == ConciliacaoFaturamentoRemessa.conciliacao_id,
        )
        .where(
            ConciliacaoFaturamentoRemessa.cd_remessa.in_(codigos_remessa),
            *(
                [ConciliacaoFaturamento.ativo.is_(True)]
                if not incluir_inativas
                else []
            ),
        )
        .order_by(
            ConciliacaoFaturamentoRemessa.cd_remessa,
            ConciliacaoFaturamento.data_criacao.desc(),
            ConciliacaoFaturamento.id.desc(),
        )
    ).all()
    vinculos = [row[0] for row in rows]
    conciliacoes_por_id = {row[1].id: row[1] for row in rows}
    conciliacoes = list(conciliacoes_por_id.values())
    ids = set(conciliacoes_por_id)
    vinculos_por_conciliacao = {conciliacao_id: [] for conciliacao_id in ids}
    vinculos_por_remessa = {codigo: [] for codigo in codigos_remessa}
    for vinculo in vinculos:
        vinculos_por_conciliacao[vinculo.conciliacao_id].append(vinculo)
        vinculos_por_remessa[vinculo.cd_remessa].append(vinculo)

    recebimentos = list(
        session.scalars(
            select(RecebimentoRemessa)
            .where(
                RecebimentoRemessa.conciliacao_id.in_(ids),
                RecebimentoRemessa.cd_remessa.in_(codigos_remessa),
            )
            .order_by(RecebimentoRemessa.data_registro)
        )
    )
    recebimentos_por_vinculo = {}
    for recebimento in recebimentos:
        recebimentos_por_vinculo.setdefault(
            (recebimento.conciliacao_id, recebimento.cd_remessa),
            [],
        ).append(recebimento)

    remessas = {
        remessa.cd_remessa: remessa
        for remessa in session.scalars(
            select(RemessaFinanceira).where(
                RemessaFinanceira.cd_remessa.in_(codigos_remessa)
            )
        )
    }
    processos = {
        processo.cd_remessa: processo
        for processo in session.scalars(
            select(ProcessoConciliacaoRemessa).where(
                ProcessoConciliacaoRemessa.cd_remessa.in_(codigos_remessa)
            )
        )
    }

    auditorias, auditorias_por_conciliacao = (
        _auditorias_linha_tempo_conciliacoes(
            session,
            conciliacoes,
            vinculos_por_conciliacao,
        )
    )
    numeros_nfse_por_conciliacao = {
        conciliacao_id: numero_nfse
        for conciliacao_id, numero_nfse in session.execute(
            select(
                ConciliacaoFaturamento.id,
                ConciliacaoFaturamento.numero_nfse,
            ).where(
                ConciliacaoFaturamento.id.in_(
                    {
                        auditoria.conciliacao_id
                        for auditoria in auditorias
                    }
                )
            )
        )
    }

    usuarios_ids = {
        usuario_id
        for conciliacao in conciliacoes
        for usuario_id in (
            conciliacao.usuario_id,
            conciliacao.usuario_atualizacao_id,
            conciliacao.usuario_inativacao_id,
        )
        if usuario_id is not None
    }
    usuarios_ids.update(recebimento.usuario_id for recebimento in recebimentos)
    usuarios_ids.update(auditoria.usuario_id for auditoria in auditorias)
    usuarios = {
        usuario.id: usuario
        for usuario in session.scalars(
            select(Usuario).where(Usuario.id.in_(usuarios_ids))
        )
    }

    cards = []
    for cd_remessa in codigos_remessa:  # noqa: PLR1704
        vinculos_remessa = vinculos_por_remessa[cd_remessa]
        notas = []
        auditorias_remessa = {}
        for vinculo in vinculos_remessa:
            conciliacao = conciliacoes_por_id[vinculo.conciliacao_id]
            recebimentos_vinculo = recebimentos_por_vinculo.get(
                (conciliacao.id, cd_remessa),
                [],
            )
            notas.append(
                {
                    'id': conciliacao.id,
                    'numero_nfse': conciliacao.numero_nfse,
                    'tipo_conciliacao': vinculo.tp_conciliacao,
                    'valor_nfse': _money(conciliacao.valor_nfse),
                    'valor_vinculado_remessa': _money(vinculo.valor_total),
                    'valor_alocado_nfse': _valor_alocado_vinculo(vinculo),
                    'valor_impostos': _valor_impostos_vinculo(vinculo),
                    'valor_glosado': _money(vinculo.valor_glosado),
                    'data_previsao_recebimento': (
                        conciliacao.data_previsao_recebimento
                    ),
                    'data_recebimento': conciliacao.data_recebimento,
                    'data_criacao': conciliacao.data_criacao,
                    'data_atualizacao': conciliacao.data_atualizacao,
                    'data_inativacao': conciliacao.data_inativacao,
                    'ativo': conciliacao.ativo,
                    'situacao_recebimento': (
                        'recebido'
                        if recebimentos_vinculo
                        else 'sem_recebimento'
                    ),
                    'usuario_criacao': _usuario_operacao_publico(
                        usuarios.get(conciliacao.usuario_id)
                    ),
                    'usuario_atualizacao': _usuario_operacao_publico(
                        usuarios.get(conciliacao.usuario_atualizacao_id)
                    ),
                    'usuario_inativacao': _usuario_operacao_publico(
                        usuarios.get(conciliacao.usuario_inativacao_id)
                    ),
                    'recebimentos': [
                        {
                            'id': recebimento.id,
                            'cd_remessa': recebimento.cd_remessa,
                            'data_recebimento': (
                                recebimento.data_recebimento
                            ),
                            'valor_recebido': _money(
                                recebimento.valor_recebido
                            ),
                            'conta_bancaria_id': (
                                recebimento.conta_bancaria_id
                            ),
                            'conta_plano_contas': (
                                recebimento.conta_plano_contas
                            ),
                            'conta_centro_custo': (
                                recebimento.conta_centro_custo
                            ),
                            'lancamento_extrato_id': (
                                recebimento.lancamento_extrato_id
                            ),
                            'data_registro': recebimento.data_registro,
                            'usuario': _usuario_operacao_publico(
                                usuarios.get(recebimento.usuario_id)
                            ),
                        }
                        for recebimento in recebimentos_vinculo
                    ],
                }
            )
            for auditoria in auditorias_por_conciliacao[conciliacao.id]:
                auditorias_remessa[auditoria.id] = auditoria
        remessa = remessas.get(cd_remessa)
        ultima_conciliacao = conciliacoes_por_id[
            vinculos_remessa[0].conciliacao_id
        ]
        recebimentos_remessa = [
            recebimento
            for recebimento in recebimentos
            if recebimento.cd_remessa == cd_remessa
        ]
        eventos = sorted(
            auditorias_remessa.values(),
            key=lambda auditoria: (
                auditoria.data_operacao,
                auditoria.id,
            ),
            reverse=True,
        )
        cards.append(
            {
                'cd_remessa': cd_remessa,
                'convenio': (
                    remessa.convenio
                    if remessa is not None
                    else ultima_conciliacao.convenio
                ),
                'cnpj_convenio': (
                    remessa.cnpj_convenio
                    if remessa is not None
                    else ultima_conciliacao.cnpj_convenio
                ),
                'processo_recebimento': (
                    processos[cd_remessa].processo_recebimento
                    if cd_remessa in processos
                    else ultima_conciliacao.processo_recebimento
                ),
                'data_competencia': (
                    remessa.data_competencia if remessa is not None else None
                ),
                'valor_remessa': (
                    totais_remessas_hpc.get(
                        cd_remessa,
                        (
                            _money(remessa.valor_total)
                            if remessa is not None
                            else sum(
                                (
                                    _money(vinculo.valor_total)
                                    for vinculo in vinculos_remessa
                                ),
                                Decimal('0.00'),
                            )
                        ),
                    )
                ),
                'valor_alocado_nfse': sum(
                    (
                        _valor_alocado_vinculo(vinculo)
                        for vinculo in vinculos_remessa
                        if conciliacoes_por_id[vinculo.conciliacao_id].ativo
                    ),
                    Decimal('0.00'),
                ),
                'valor_impostos': sum(
                    (
                        _valor_impostos_vinculo(vinculo)
                        for vinculo in vinculos_remessa
                        if conciliacoes_por_id[
                            vinculo.conciliacao_id
                        ].ativo
                    ),
                    Decimal('0.00'),
                ),
                'valor_glosado': sum(
                    (
                        _money(vinculo.valor_glosado)
                        for vinculo in vinculos_remessa
                        if conciliacoes_por_id[vinculo.conciliacao_id].ativo
                    ),
                    Decimal('0.00'),
                ),
                'ativo': any(nota['ativo'] for nota in notas),
                'situacao_recebimento': (
                    'recebido'
                    if recebimentos_remessa
                    else 'sem_recebimento'
                ),
                'notas': notas,
                'auditoria': [
                    {
                        'id': auditoria.id,
                        'conciliacao_origem_id': auditoria.conciliacao_id,
                        'numero_nfse': numeros_nfse_por_conciliacao[
                            auditoria.conciliacao_id
                        ],
                        'acao': auditoria.acao,
                        'usuario': _usuario_operacao_publico(
                            usuarios.get(auditoria.usuario_id)
                        ),
                        'dados_anteriores': auditoria.dados_anteriores,
                        'dados_novos': auditoria.dados_novos,
                        'data_operacao': auditoria.data_operacao,
                    }
                    for auditoria in eventos
                ],
            }
        )
    return {
        'conciliacoes': cards,
        'total': total,
        'total_ativas': total_ativas,
        'total_inativas': total_inativas,
        'total_recebidas': total_recebidas,
        'total_sem_recebimento': total_pendentes,
        'limit': limit,
        'offset': offset,
    }


@router.put(
    '/conciliacao-faturamento/conciliacoes/{conciliacao_id}',
    status_code=HTTPStatus.OK,
    response_model=ConciliacaoAlteracaoPublic,
)
def editar_conciliacao_faturamento(
    conciliacao_id: int,
    payload: ConciliacaoFaturamentoUpdate,
    usuario_atual: ValidaUsuarioAtual,
    session: SessionPostgres,
    session_oracle: Session = Depends(get_session_oracle),
):
    conciliacao = session.scalar(
        select(ConciliacaoFaturamento)
        .where(ConciliacaoFaturamento.id == conciliacao_id)
        .with_for_update()
    )
    if conciliacao is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Conciliação não encontrada.',
        )
    if not conciliacao.ativo:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='Conciliações inativas não podem ser alteradas.',
        )
    recebimento_existe = session.scalar(
        select(RecebimentoRemessa.id)
        .where(RecebimentoRemessa.conciliacao_id == conciliacao_id)
        .limit(1)
    )
    if recebimento_existe or conciliacao.data_recebimento is not None:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail=(
                'Conciliações com recebimento bancário não podem ser '
                'alteradas por este fluxo.'
            ),
        )
    vinculos_alvo = list(
        session.scalars(
            select(ConciliacaoFaturamentoRemessa).where(
                ConciliacaoFaturamentoRemessa.conciliacao_id
                == conciliacao_id
            )
        )
    )
    agora = datetime.now(ZoneInfo('America/Sao_Paulo')).replace(tzinfo=None)
    conciliacoes_afetadas = {conciliacao.id: conciliacao}
    if payload.processo_recebimento is not None:
        codigos_remessa = {vinculo.cd_remessa for vinculo in vinculos_alvo}
        if codigos_remessa:
            for item in session.scalars(
                select(ConciliacaoFaturamento)
                .join(
                    ConciliacaoFaturamentoRemessa,
                    ConciliacaoFaturamentoRemessa.conciliacao_id
                    == ConciliacaoFaturamento.id,
                )
                .where(
                    ConciliacaoFaturamentoRemessa.cd_remessa.in_(
                        codigos_remessa
                    ),
                    ConciliacaoFaturamento.ativo.is_(True),
                )
                .distinct()
            ):
                conciliacoes_afetadas[item.id] = item
            for processo in session.scalars(
                select(ProcessoConciliacaoRemessa).where(
                    ProcessoConciliacaoRemessa.cd_remessa.in_(
                        codigos_remessa
                    )
                )
            ):
                processo.processo_recebimento = (
                    payload.processo_recebimento
                )
                processo.usuario_atualizacao_id = usuario_atual.id
                processo.data_atualizacao = agora

    snapshots_anteriores = {
        item.id: _snapshot_conciliacao(
            item,
            vinculos_alvo if item.id == conciliacao.id else None,
        )
        for item in conciliacoes_afetadas.values()
    }
    if payload.processo_recebimento is not None:
        conciliacao.processo_recebimento = payload.processo_recebimento
    _atualizar_valores_conciliacao(
        session,
        session_oracle,
        conciliacao,
        vinculos_alvo,
        payload,
    )
    if payload.data_previsao_recebimento is not None:
        conciliacao.data_previsao_recebimento = (
            payload.data_previsao_recebimento
        )
    for item in conciliacoes_afetadas.values():
        if payload.processo_recebimento is not None:
            item.processo_recebimento = payload.processo_recebimento
        item.usuario_atualizacao_id = usuario_atual.id
        item.data_atualizacao = agora
        _registrar_auditoria_conciliacao(
            session,
            item.id,
            'edicao',
            usuario_atual.id,
            dados_anteriores=snapshots_anteriores[item.id],
            dados_novos=_snapshot_conciliacao(
                item,
                vinculos_alvo if item.id == conciliacao.id else None,
            ),
        )
    session.commit()
    session.refresh(conciliacao)
    return _conciliacao_alteracao_publica(
        conciliacao,
        usuario_atual.id,
        agora,
        'Conciliação atualizada com sucesso.',
    )


@router.delete(
    '/conciliacao-faturamento/conciliacoes/{conciliacao_id}',
    status_code=HTTPStatus.OK,
    response_model=ConciliacaoAlteracaoPublic,
)
def inativar_conciliacao_faturamento(
    conciliacao_id: int,
    usuario_atual: ValidaUsuarioAtual,
    session: SessionPostgres,
):
    conciliacao = session.scalar(
        select(ConciliacaoFaturamento)
        .where(ConciliacaoFaturamento.id == conciliacao_id)
        .with_for_update()
    )
    if conciliacao is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Conciliação não encontrada.',
        )
    if not conciliacao.ativo:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='A conciliação já está inativa.',
        )
    recebimento_existe = session.scalar(
        select(RecebimentoRemessa.id)
        .where(RecebimentoRemessa.conciliacao_id == conciliacao_id)
        .limit(1)
    )
    if recebimento_existe or conciliacao.data_recebimento is not None:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail=(
                'Conciliações com recebimento bancário não podem ser '
                'inativadas.'
            ),
        )
    vinculos = list(
        session.scalars(
            select(ConciliacaoFaturamentoRemessa).where(
                ConciliacaoFaturamentoRemessa.conciliacao_id
                == conciliacao_id
            )
        )
    )
    dados_anteriores = _snapshot_conciliacao(conciliacao, vinculos)
    agora = datetime.now(ZoneInfo('America/Sao_Paulo')).replace(tzinfo=None)
    conciliacao.ativo = False
    conciliacao.usuario_inativacao_id = usuario_atual.id
    conciliacao.data_inativacao = agora
    conciliacao.usuario_atualizacao_id = usuario_atual.id
    conciliacao.data_atualizacao = agora
    ids_vinculos = {vinculo.id for vinculo in vinculos}
    if ids_vinculos:
        for registro in session.scalars(
            select(RegistroGlosa).where(
                RegistroGlosa.conciliacao_remessa_id.in_(ids_vinculos)
            )
        ):
            registro.sn_ativo = 'not'
    _registrar_auditoria_conciliacao(
        session,
        conciliacao.id,
        'inativacao',
        usuario_atual.id,
        dados_anteriores=dados_anteriores,
        dados_novos=_snapshot_conciliacao(conciliacao, vinculos),
    )
    session.commit()
    session.refresh(conciliacao)
    return _conciliacao_alteracao_publica(
        conciliacao,
        usuario_atual.id,
        agora,
        'Conciliação inativada com sucesso.',
    )


@router.get(
    '/conciliacao-faturamento/sem-recebimento',
    status_code=HTTPStatus.OK,
    response_model=ConciliacoesSemRecebimentoList,
)
def consultar_conciliacoes_sem_recebimento(  # noqa: PLR0913, PLR0915, PLR1704
    usuario_atual: ValidaUsuarioAtual,
    session: SessionPostgres,
    session_oracle: Session = Depends(get_session_oracle),
    q: str | None = Query(default=None, max_length=100),
    numero_nfse: str | None = None,
    cd_remessa: str | None = None,
    convenio: str | None = None,
    processo_recebimento: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    del usuario_atual
    valor_recebivel = case(
        (
            ConciliacaoFaturamentoRemessa.valor_alocado_nfse > 0,
            ConciliacaoFaturamentoRemessa.valor_alocado_nfse,
        ),
        else_=(
            ConciliacaoFaturamentoRemessa.valor_total
            - ConciliacaoFaturamentoRemessa.valor_glosado
        ),
    )
    recebimentos_por_vinculo = (
        select(
            RecebimentoRemessa.conciliacao_id.label('conciliacao_id'),
            RecebimentoRemessa.cd_remessa.label('cd_remessa'),
            func.sum(RecebimentoRemessa.valor_recebido).label(
                'valor_recebido'
            ),
        )
        .group_by(
            RecebimentoRemessa.conciliacao_id,
            RecebimentoRemessa.cd_remessa,
        )
        .subquery()
    )
    valor_recebido_vinculo = func.coalesce(
        recebimentos_por_vinculo.c.valor_recebido,
        0,
    )
    saldo_vinculo = valor_recebivel - valor_recebido_vinculo
    pending_filters = [
        ConciliacaoFaturamento.ativo.is_(True),
        saldo_vinculo > 0,
    ]
    vinculos_pendentes = (
        select(
            ConciliacaoFaturamentoRemessa.cd_remessa.label('cd_remessa'),
            ConciliacaoFaturamentoRemessa.conciliacao_id.label(
                'conciliacao_id'
            ),
            saldo_vinculo.label('valor_pendente'),
            ConciliacaoFaturamento.numero_nfse.label('numero_nfse'),
            ConciliacaoFaturamento.convenio.label('convenio'),
            ConciliacaoFaturamento.processo_recebimento.label(
                'processo_recebimento'
            ),
            ConciliacaoFaturamento.data_previsao_recebimento.label(
                'data_previsao_recebimento'
            ),
        )
        .join(
            ConciliacaoFaturamento,
            ConciliacaoFaturamento.id
            == ConciliacaoFaturamentoRemessa.conciliacao_id,
        )
        .outerjoin(
            recebimentos_por_vinculo,
            and_(
                recebimentos_por_vinculo.c.conciliacao_id
                == ConciliacaoFaturamentoRemessa.conciliacao_id,
                recebimentos_por_vinculo.c.cd_remessa
                == ConciliacaoFaturamentoRemessa.cd_remessa,
            ),
        )
        .where(*pending_filters)
        .subquery()
    )
    remessas_query = select(
        vinculos_pendentes.c.cd_remessa,
        func.count(vinculos_pendentes.c.conciliacao_id).label(
            'quantidade_nfses'
        ),
        func.sum(vinculos_pendentes.c.valor_pendente).label(
            'valor_pendente'
        ),
        func.min(
            vinculos_pendentes.c.data_previsao_recebimento
        ).label('primeira_previsao'),
    ).group_by(vinculos_pendentes.c.cd_remessa)
    filtros_separados = (
        (numero_nfse, vinculos_pendentes.c.numero_nfse),
        (convenio, vinculos_pendentes.c.convenio),
        (
            processo_recebimento,
            vinculos_pendentes.c.processo_recebimento,
        ),
        (
            cd_remessa,
            cast(vinculos_pendentes.c.cd_remessa, String),
        ),
    )
    for valor_filtro, coluna in filtros_separados:
        termo_filtro = (valor_filtro or '').strip()
        if not termo_filtro:
            continue
        remessas_correspondentes = select(
            vinculos_pendentes.c.cd_remessa
        ).where(
            coluna.ilike(f'%{termo_filtro}%')
        ).distinct()
        remessas_query = remessas_query.where(
            vinculos_pendentes.c.cd_remessa.in_(remessas_correspondentes)
        )
    termo = (q or '').strip()
    if termo:
        pattern = f'%{termo}%'
        remessas_correspondentes = select(
            vinculos_pendentes.c.cd_remessa
        ).where(
            or_(
                vinculos_pendentes.c.numero_nfse.ilike(pattern),
                vinculos_pendentes.c.convenio.ilike(pattern),
                vinculos_pendentes.c.processo_recebimento.ilike(pattern),
                cast(vinculos_pendentes.c.cd_remessa, String).ilike(pattern),
            )
        ).distinct()
        remessas_query = remessas_query.where(
            vinculos_pendentes.c.cd_remessa.in_(remessas_correspondentes)
        )

    remessas_pendentes = remessas_query.subquery()
    total, valor_total_pendente = session.execute(
        select(
            func.count(remessas_pendentes.c.cd_remessa),
            func.coalesce(func.sum(remessas_pendentes.c.valor_pendente), 0),
        ).select_from(remessas_pendentes)
    ).one()
    valor_total_recebido = session.scalar(
        select(
            func.coalesce(func.sum(RecebimentoRemessa.valor_recebido), 0)
        ).where(
            RecebimentoRemessa.cd_remessa.in_(
                select(remessas_pendentes.c.cd_remessa)
            )
        )
    )
    codigos_remessa = list(
        session.scalars(
            select(remessas_pendentes.c.cd_remessa)
            .order_by(
                remessas_pendentes.c.primeira_previsao,
                remessas_pendentes.c.cd_remessa,
            )
            .offset(offset)
            .limit(limit)
        )
    )
    if not codigos_remessa:
        return {
            'conciliacoes': [],
            'total': int(total),
            'total_remessas_sem_recebimento': int(total),
            'valor_total_recebido': _money(valor_total_recebido),
            'valor_total_pendente': _money(valor_total_pendente),
            'limit': limit,
            'offset': offset,
        }
    totais_remessas_hpc = {}
    try:
        totais_remessas_hpc = sincronizar_totais_remessas_financeiras(
            session,
            session_oracle,
            set(codigos_remessa),
        )
    except SQLAlchemyError:
        # Mantém a última posição persistida quando o Oracle está indisponível.
        pass

    note_rows = session.execute(
        select(
            ConciliacaoFaturamentoRemessa,
            ConciliacaoFaturamento,
            valor_recebido_vinculo.label('valor_recebido'),
            saldo_vinculo.label('valor_pendente'),
        )
        .join(
            ConciliacaoFaturamento,
            ConciliacaoFaturamento.id
            == ConciliacaoFaturamentoRemessa.conciliacao_id,
        )
        .outerjoin(
            recebimentos_por_vinculo,
            and_(
                recebimentos_por_vinculo.c.conciliacao_id
                == ConciliacaoFaturamentoRemessa.conciliacao_id,
                recebimentos_por_vinculo.c.cd_remessa
                == ConciliacaoFaturamentoRemessa.cd_remessa,
            ),
        )
        .where(
            ConciliacaoFaturamentoRemessa.cd_remessa.in_(codigos_remessa),
            ConciliacaoFaturamento.ativo.is_(True),
        )
        .order_by(
            ConciliacaoFaturamento.data_previsao_recebimento,
            ConciliacaoFaturamento.id,
        )
    ).all()
    vinculos_por_remessa: dict[int, list[tuple]] = {
        cd_remessa: [] for cd_remessa in codigos_remessa
    }
    for vinculo, conciliacao, valor_recebido, valor_pendente in note_rows:
        vinculos_por_remessa[vinculo.cd_remessa].append(
            (
                vinculo,
                conciliacao,
                _money(valor_recebido),
                _money(valor_pendente),
            )
        )
    recebimentos_anteriores = list(
        session.scalars(
            select(RecebimentoRemessa)
            .where(
                RecebimentoRemessa.conciliacao_id.in_(
                    {
                        conciliacao.id
                        for _, conciliacao, _, _ in note_rows
                    }
                ),
                RecebimentoRemessa.cd_remessa.in_(codigos_remessa),
            )
            .order_by(
                RecebimentoRemessa.data_recebimento,
                RecebimentoRemessa.id,
            )
        )
    )
    recebimentos_por_nfse = {}
    for recebimento in recebimentos_anteriores:
        recebimentos_por_nfse.setdefault(
            (recebimento.conciliacao_id, recebimento.cd_remessa),
            [],
        ).append(recebimento)
    lancamentos_ids = {
        recebimento.lancamento_extrato_id
        for recebimento in recebimentos_anteriores
        if recebimento.lancamento_extrato_id is not None
    }
    lancamentos_por_id = {
        lancamento.id: lancamento
        for lancamento in session.scalars(
            select(LancamentoExtratoBancario).where(
                LancamentoExtratoBancario.id.in_(lancamentos_ids)
            )
        )
    }
    valores_recebidos = {
        int(cd_remessa): _money(valor)
        for cd_remessa, valor in session.execute(
            select(
                RecebimentoRemessa.cd_remessa,
                func.sum(RecebimentoRemessa.valor_recebido),
            )
            .where(RecebimentoRemessa.cd_remessa.in_(codigos_remessa))
            .group_by(RecebimentoRemessa.cd_remessa)
        )
    }
    remessas = {
        remessa.cd_remessa: remessa
        for remessa in session.scalars(
            select(RemessaFinanceira).where(
                RemessaFinanceira.cd_remessa.in_(codigos_remessa)
            )
        )
    }
    processos = {
        processo.cd_remessa: processo
        for processo in session.scalars(
            select(ProcessoConciliacaoRemessa).where(
                ProcessoConciliacaoRemessa.cd_remessa.in_(codigos_remessa)
            )
        )
    }

    hoje = datetime.now(ZoneInfo('America/Sao_Paulo')).date()
    conciliacoes = []
    for cd_remessa in codigos_remessa:  # noqa: PLR1704
        vinculos = vinculos_por_remessa[cd_remessa]
        remessa = remessas.get(cd_remessa)
        conciliacao_recente = max(
            (conciliacao for _, conciliacao, _, _ in vinculos),
            key=lambda conciliacao: (
                conciliacao.data_criacao,
                conciliacao.id,
            ),
        )
        notas = []
        for (
            vinculo,
            conciliacao,
            valor_recebido_nfse,
            valor_pendente_nfse,
        ) in vinculos:
            saldo_pendente_nfse = max(
                valor_pendente_nfse,
                Decimal('0.00'),
            )
            saldo_historico_nfse = _money(
                valor_recebido_nfse + saldo_pendente_nfse
            )
            recebimentos_nfse = []
            for recebimento in recebimentos_por_nfse.get(
                (conciliacao.id, vinculo.cd_remessa),
                [],
            ):
                saldo_historico_nfse = max(
                    saldo_historico_nfse
                    - _money(recebimento.valor_recebido),
                    Decimal('0.00'),
                )
                lancamento = lancamentos_por_id.get(
                    recebimento.lancamento_extrato_id
                )
                recebimentos_nfse.append(
                    {
                        'id': recebimento.id,
                        'data_recebimento': recebimento.data_recebimento,
                        'valor_recebido': _money(
                            recebimento.valor_recebido
                        ),
                        'saldo_financeiro': _money(
                            saldo_historico_nfse
                        ),
                        'conta_bancaria_id': (
                            recebimento.conta_bancaria_id
                        ),
                        'conta_plano_contas': (
                            recebimento.conta_plano_contas
                        ),
                        'conta_centro_custo': (
                            recebimento.conta_centro_custo
                        ),
                        'lancamento_extrato_id': (
                            recebimento.lancamento_extrato_id
                        ),
                        'lancamento_extrato': (
                            {
                                'id': lancamento.id,
                                'conta_bancaria_id': (
                                    lancamento.conta_bancaria_id
                                ),
                                'data_lancamento': (
                                    lancamento.data_lancamento
                                ),
                                'valor': _money(lancamento.valor),
                                'descricao': lancamento.descricao,
                                'documento': lancamento.documento,
                            }
                            if lancamento is not None
                            else None
                        ),
                        'data_registro': recebimento.data_registro,
                    }
                )
            dias_em_atraso = (
                max(
                    (hoje - conciliacao.data_previsao_recebimento).days,
                    0,
                )
                if saldo_pendente_nfse > 0
                else 0
            )
            notas.append(
                {
                    'id': conciliacao.id,
                    'numero_nfse': conciliacao.numero_nfse,
                    'tp_conciliacao': vinculo.tp_conciliacao,
                    'data_previsao_recebimento': (
                        conciliacao.data_previsao_recebimento
                    ),
                    'data_criacao': conciliacao.data_criacao,
                    'valor_nfse': _money(conciliacao.valor_nfse),
                    'valor_vinculado_remessa': _money(
                        vinculo.valor_total
                    ),
                    'valor_impostos': _valor_impostos_vinculo(vinculo),
                    'valor_glosado': _money(vinculo.valor_glosado),
                    'valor_recebido': valor_recebido_nfse,
                    'valor_pendente': saldo_pendente_nfse,
                    'situacao': (
                        'recebido'
                        if saldo_pendente_nfse == 0
                        else (
                            'recebimento_parcial'
                            if valor_recebido_nfse > 0
                            else 'sem_recebimento'
                        )
                    ),
                    'em_atraso': dias_em_atraso > 0,
                    'dias_em_atraso': dias_em_atraso,
                    'recebimentos': recebimentos_nfse,
                }
            )
        valor_pendente_total = sum(
            (nota['valor_pendente'] for nota in notas),
            Decimal('0.00'),
        )
        valor_recebido = valores_recebidos.get(
            cd_remessa,
            Decimal('0.00'),
        )
        dias_em_atraso = max(
            (
                nota['dias_em_atraso']
                for nota in notas
                if nota['valor_pendente'] > 0
            ),
            default=0,
        )
        valor_remessa = totais_remessas_hpc.get(
            cd_remessa,
            (
                _money(remessa.valor_total)
                if remessa is not None
                else sum(
                    (
                        _money(vinculo.valor_total)
                        for vinculo, _, _, _ in vinculos
                    ),
                    Decimal('0.00'),
                )
            ),
        )
        valor_total_glosas = _money(
            sum(
                (nota['valor_glosado'] for nota in notas),
                Decimal('0.00'),
            )
        )
        valor_total_impostos = _money(
            sum(
                (
                    _valor_impostos_vinculo(vinculo)
                    for vinculo, _, _, _ in vinculos
                ),
                Decimal('0.00'),
            )
        )
        valor_liquido = _money(
            sum(
                (
                    _valor_alocado_vinculo(vinculo)
                    for vinculo, _, _, _ in vinculos
                ),
                Decimal('0.00'),
            )
        )
        conciliacoes.append(
            {
                'cd_remessa': cd_remessa,
                'convenio': (
                    remessa.convenio
                    if remessa is not None
                    else conciliacao_recente.convenio
                ),
                'cnpj_convenio': (
                    remessa.cnpj_convenio
                    if remessa is not None
                    else conciliacao_recente.cnpj_convenio
                ),
                'processo_recebimento': (
                    processos[cd_remessa].processo_recebimento
                    if cd_remessa in processos
                    else conciliacao_recente.processo_recebimento
                ),
                'data_competencia': (
                    remessa.data_competencia if remessa is not None else None
                ),
                'valor_remessa': valor_remessa,
                'quantidade_nfses_sem_recebimento': sum(
                    nota['valor_pendente'] > 0 for nota in notas
                ),
                'valor_total_glosas': valor_total_glosas,
                'valor_total_impostos': valor_total_impostos,
                'valor_liquido': valor_liquido,
                'valor_recebido': _money(valor_recebido),
                'valor_pendente': _money(valor_pendente_total),
                'situacao': (
                    'sem_recebimento'
                    if valor_recebido == 0
                    else 'recebimento_parcial'
                ),
                'em_atraso': dias_em_atraso > 0,
                'dias_em_atraso': dias_em_atraso,
                'notas': notas,
            }
        )

    return {
        'conciliacoes': conciliacoes,
        'total': int(total),
        'total_remessas_sem_recebimento': int(total),
        'valor_total_recebido': _money(valor_total_recebido),
        'valor_total_pendente': _money(valor_total_pendente),
        'limit': limit,
        'offset': offset,
    }


@router.post(
    '/conciliacao-faturamento/recebimentos-remessas',
    status_code=HTTPStatus.CREATED,
    response_model=RecebimentoRemessaPublic,
)
def registrar_recebimento_remessa(
    payload: RecebimentoRemessaCreate,
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
    session_oracle: Session = Depends(get_session_oracle),
):
    vinculo_query = (
        select(
            ConciliacaoFaturamento,
            ConciliacaoFaturamentoRemessa,
        )
        .join(
            ConciliacaoFaturamentoRemessa,
            ConciliacaoFaturamentoRemessa.conciliacao_id
            == ConciliacaoFaturamento.id,
        )
        .where(
            ConciliacaoFaturamento.numero_nfse == payload.numero_nfse,
            ConciliacaoFaturamentoRemessa.cd_remessa
            == payload.cd_remessa,
            ConciliacaoFaturamento.ativo.is_(True),
        )
    )
    if payload.conciliacao_id is not None:
        vinculo_query = vinculo_query.where(
            ConciliacaoFaturamento.id == payload.conciliacao_id
        )
    vinculo = session_postgres.execute(
        vinculo_query.order_by(ConciliacaoFaturamento.id.desc())
        .limit(1)
        .with_for_update()
    ).one_or_none()
    if vinculo is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='A NFS-e não está conciliada com a remessa informada.',
        )

    conciliacao, remessa_conciliada = vinculo
    dados_anteriores = _snapshot_conciliacao(
        conciliacao,
        [remessa_conciliada],
    )
    valor_total_vinculo = _valor_alocado_vinculo(remessa_conciliada)
    valor_recebido_anterior = _total_recebido_vinculo(
        session_postgres,
        conciliacao.id,
        remessa_conciliada.cd_remessa,
    )
    saldo_vinculo = max(
        valor_total_vinculo - valor_recebido_anterior,
        Decimal('0.00'),
    )
    if _money(payload.valor_recebido) > saldo_vinculo:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                f'O valor recebido da NFS-e {conciliacao.numero_nfse} '
                f'excede o saldo em aberto de '
                f'{_valor_reais_mensagem(saldo_vinculo)}.'
            ),
        )
    lancamento = _validar_dados_bancarios(
        payload,
        session_postgres,
        session_oracle,
    )
    valor_original = session_postgres.scalar(
        select(func.max(ConciliacaoFaturamentoRemessa.valor_total)).where(
            ConciliacaoFaturamentoRemessa.cd_remessa == payload.cd_remessa
        )
    )
    dados_remessa = {
        'cd_remessa': remessa_conciliada.cd_remessa,
        'convenio': remessa_conciliada.convenio,
        'cnpj_convenio': remessa_conciliada.cnpj_convenio,
        'valor_total': valor_original,
    }

    try:
        remessa = _obter_ou_criar_remessa_financeira(
            session_postgres,
            dados_remessa,
        )
        recebimento, valor_total_recebido = _registrar_recebimento_remessa(
            session=session_postgres,
            remessa=remessa,
            conciliacao_id=conciliacao.id,
            numero_nfse=conciliacao.numero_nfse,
            data_recebimento=payload.data_recebimento,
            valor_recebido=payload.valor_recebido,
            usuario_id=usuario_atual.id,
            conta_bancaria_id=payload.conta_bancaria_id,
            conta_plano_contas=payload.conta_plano_contas,
            conta_centro_custo=payload.conta_centro_custo,
            lancamento_extrato_id=payload.lancamento_extrato_id,
        )
        recebimento_integral_nfse = (
            valor_recebido_anterior + _money(payload.valor_recebido)
            == valor_total_vinculo
        )
        if recebimento_integral_nfse:
            conciliacao.data_recebimento = payload.data_recebimento
            conciliacao.conta_bancaria_id = payload.conta_bancaria_id
            conciliacao.conta_plano_contas = payload.conta_plano_contas
            conciliacao.conta_centro_custo = payload.conta_centro_custo
            conciliacao.lancamento_extrato_id = (
                payload.lancamento_extrato_id
            )
        conciliacao.usuario_atualizacao_id = usuario_atual.id
        conciliacao.data_atualizacao = datetime.now(
            ZoneInfo('America/Sao_Paulo')
        ).replace(tzinfo=None)
        _registrar_auditoria_conciliacao(
            session_postgres,
            conciliacao.id,
            'recebimento',
            usuario_atual.id,
            dados_anteriores=dados_anteriores,
            dados_novos=_snapshot_conciliacao(
                conciliacao,
                [remessa_conciliada],
                recebimento,
            ),
        )
        if lancamento is not None:
            lancamento.conciliado = True
        session_postgres.commit()
        session_postgres.refresh(recebimento)
        session_postgres.refresh(remessa)
    except HTTPException:
        session_postgres.rollback()
        raise
    except IntegrityError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='Nao foi possivel registrar o recebimento da NFS-e.',
        ) from exc

    return _recebimento_remessa_publico(
        recebimento,
        remessa,
        valor_total_recebido,
        _valores_acatados_por_remessa(
            session_postgres,
            {remessa.cd_remessa},
        ).get(remessa.cd_remessa, Decimal('0.00')),
    )


@router.patch(
    '/conciliacao-faturamento/recebimentos-remessas/{recebimento_id}',
    status_code=HTTPStatus.OK,
    response_model=RecebimentoRemessaPublic,
)
def editar_recebimento_remessa(
    recebimento_id: int,
    payload: RecebimentoRemessaUpdate,
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
    session_oracle: Session = Depends(get_session_oracle),
):
    recebimento, conciliacao, vinculo, remessa = (
        _contexto_recebimento_remessa(session_postgres, recebimento_id)
    )
    dados_anteriores = _snapshot_conciliacao(
        conciliacao,
        [vinculo],
        recebimento,
    )
    valor_atual = _money(recebimento.valor_recebido)
    valor_novo = _money(payload.valor_recebido)
    valor_outros_nfse = max(
        _total_recebido_vinculo(
            session_postgres,
            conciliacao.id,
            vinculo.cd_remessa,
        )
        - valor_atual,
        Decimal('0.00'),
    )
    saldo_nfse_com_parcela_atual = max(
        _valor_alocado_vinculo(vinculo) - valor_outros_nfse,
        Decimal('0.00'),
    )
    if valor_novo > saldo_nfse_com_parcela_atual:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                f'O valor recebido da NFS-e {conciliacao.numero_nfse} '
                f'excede o saldo disponível de '
                f'{_valor_reais_mensagem(saldo_nfse_com_parcela_atual)}.'
            ),
        )
    valor_outros_remessa = max(
        _total_recebido_remessa(session_postgres, remessa.cd_remessa)
        - valor_atual,
        Decimal('0.00'),
    )
    valor_acatado = _valores_acatados_por_remessa(
        session_postgres,
        {remessa.cd_remessa},
    ).get(remessa.cd_remessa, Decimal('0.00'))
    saldo_remessa_com_parcela_atual = max(
        _money(remessa.valor_total) - valor_acatado - valor_outros_remessa,
        Decimal('0.00'),
    )
    if valor_novo > saldo_remessa_com_parcela_atual:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                f'O valor recebido da remessa {remessa.cd_remessa} excede '
                f'o saldo disponível de '
                f'{_valor_reais_mensagem(saldo_remessa_com_parcela_atual)}.'
            ),
        )
    lancamento_anterior_id = recebimento.lancamento_extrato_id
    lancamento = _validar_dados_bancarios(
        payload,
        session_postgres,
        session_oracle,
        lancamento_extrato_id_atual=lancamento_anterior_id,
    )
    agora = datetime.now(ZoneInfo('America/Sao_Paulo')).replace(tzinfo=None)
    try:
        recebimento.data_recebimento = payload.data_recebimento
        recebimento.valor_recebido = valor_novo
        recebimento.conta_bancaria_id = payload.conta_bancaria_id
        recebimento.conta_plano_contas = payload.conta_plano_contas
        recebimento.conta_centro_custo = payload.conta_centro_custo
        recebimento.lancamento_extrato_id = payload.lancamento_extrato_id
        conciliacao.usuario_atualizacao_id = usuario_atual.id
        conciliacao.data_atualizacao = agora
        session_postgres.flush()
        if lancamento_anterior_id != payload.lancamento_extrato_id:
            _liberar_lancamento_financeiro(
                session_postgres,
                lancamento_anterior_id,
            )
        if lancamento is not None:
            lancamento.conciliado = True
        valor_total_recebido = _sincronizar_estado_recebimentos(
            session_postgres,
            conciliacao,
            remessa,
        )
        _registrar_auditoria_conciliacao(
            session_postgres,
            conciliacao.id,
            'edicao_recebimento',
            usuario_atual.id,
            dados_anteriores=dados_anteriores,
            dados_novos=_snapshot_conciliacao(
                conciliacao,
                [vinculo],
                recebimento,
            ),
        )
        session_postgres.commit()
        session_postgres.refresh(recebimento)
        session_postgres.refresh(remessa)
    except HTTPException:
        session_postgres.rollback()
        raise
    except IntegrityError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='Não foi possível atualizar o recebimento financeiro.',
        ) from exc
    return _recebimento_remessa_publico(
        recebimento,
        remessa,
        valor_total_recebido,
        valor_acatado,
    )


@router.delete(
    '/conciliacao-faturamento/recebimentos-remessas/{recebimento_id}',
    status_code=HTTPStatus.OK,
)
def excluir_recebimento_remessa(
    recebimento_id: int,
    usuario_atual: ValidaUsuarioAtual,
    session: SessionPostgres,
):
    recebimento, conciliacao, vinculo, remessa = (
        _contexto_recebimento_remessa(session, recebimento_id)
    )
    dados_anteriores = _snapshot_conciliacao(
        conciliacao,
        [vinculo],
        recebimento,
    )
    lancamento_extrato_id = recebimento.lancamento_extrato_id
    agora = datetime.now(ZoneInfo('America/Sao_Paulo')).replace(tzinfo=None)
    try:
        session.delete(recebimento)
        session.flush()
        _liberar_lancamento_financeiro(session, lancamento_extrato_id)
        conciliacao.usuario_atualizacao_id = usuario_atual.id
        conciliacao.data_atualizacao = agora
        valor_total_recebido = _sincronizar_estado_recebimentos(
            session,
            conciliacao,
            remessa,
        )
        _registrar_auditoria_conciliacao(
            session,
            conciliacao.id,
            'exclusao_recebimento',
            usuario_atual.id,
            dados_anteriores=dados_anteriores,
            dados_novos=_snapshot_conciliacao(conciliacao, [vinculo]),
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='Não foi possível excluir o recebimento financeiro.',
        ) from exc
    return {
        'id': recebimento_id,
        'numero_nfse': conciliacao.numero_nfse,
        'cd_remessa': remessa.cd_remessa,
        'valor_total_recebido': valor_total_recebido,
        'saldo_em_aberto': max(
            _money(remessa.valor_total) - valor_total_recebido,
            Decimal('0.00'),
        ),
        'message': 'Recebimento financeiro excluído com sucesso.',
    }


@router.post(
    '/conciliacao-faturamento',
    status_code=HTTPStatus.CREATED,
    response_model=ConciliacaoFaturamentoPublic,
)
def conciliar_faturamento(  # noqa: PLR0915
    payload: ConciliacaoFaturamentoCreate,
    usuario_atual: ValidaUsuarioAtual,
    session_postgres: SessionPostgres,
    session_oracle: Session = Depends(get_session_oracle),
):
    nota = session_postgres.scalar(_nota_pendente_query(payload.nfse_row_hash))
    if nota is None:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='A NFS-e nao existe ou ja foi conciliada.',
        )

    try:
        convenio = _convenio_da_nfse(
            nota,
            _consultar_convenios_hpc(session_oracle),
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel consultar a HPC_V_CONVENIOS.',
        ) from exc
    if convenio is None:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='Convenio da NFS-e nao encontrado na HPC_V_CONVENIOS.',
        )

    lancamento = _validar_dados_bancarios(
        payload,
        session_postgres,
        session_oracle,
    )
    remessas_por_id = _carregar_remessas_para_conciliacao(
        payload,
        convenio['cnpj_convenio'],
        session_postgres,
        session_oracle,
    )
    recursos_abertos = _recursos_abertos_por_remessa(
        session_postgres,
        set(remessas_por_id),
    )
    total_remessas, total_glosas = _calcular_totais_conciliacao(
        payload,
        remessas_por_id,
        recursos_abertos,
    )
    total_impostos = sum(
        (_money(item.valor_impostos) for item in payload.remessas),
        Decimal('0.00'),
    )

    valor_nfse = _money(nota.valor_liquido_nfse)
    if (
        total_remessas - total_glosas - total_impostos
    ).quantize(CENTAVOS) != valor_nfse:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=MENSAGEM_VALORES_DIVERGENTES,
        )

    ids_remessas_glosadas = {
        item.cd_remessa for item in payload.remessas if item.sn_glosado
    }
    itens_glosa_por_remessa = _carregar_itens_glosa_conciliacao(
        session_oracle,
        convenio['cnpj_convenio'],
        ids_remessas_glosadas,
    )

    nota_publica = _nota_publica(nota, convenio)
    conciliacao = ConciliacaoFaturamento(
        nfse_row_hash=nota.row_hash,
        numero_nfse=nota_publica['numero_nfse'],
        cnpj_convenio=nota_publica['cnpj_convenio'],
        convenio=nota_publica['convenio'],
        valor_nfse=valor_nfse,
        impostos=nota_publica['impostos'],
        processo_recebimento=payload.processo_recebimento,
        data_previsao_recebimento=payload.data_previsao_recebimento,
        usuario_id=usuario_atual.id,
        data_recebimento=payload.data_recebimento,
        conta_bancaria_id=payload.conta_bancaria_id,
        conta_plano_contas=payload.conta_plano_contas,
        conta_centro_custo=payload.conta_centro_custo,
        lancamento_extrato_id=payload.lancamento_extrato_id,
    )
    conciliacao.data_criacao = datetime.now(
        ZoneInfo('America/Sao_Paulo')
    ).replace(tzinfo=None)

    try:
        session_postgres.add(conciliacao)
        session_postgres.flush()
        vinculos_criados = []
        recebimentos_criados = []
        for item in payload.remessas:
            remessa = remessas_por_id[item.cd_remessa]
            tp_conciliacao = remessa.get(
                'tp_conciliacao',
                'faturamento',
            )
            valor_glosado = (
                _money(item.valor_glosado)
                if tp_conciliacao == 'recurso'
                else recursos_abertos.get(
                    item.cd_remessa,
                    _money(item.valor_glosado),
                )
            )
            valor_impostos = _money(item.valor_impostos)
            valor_alocado = (
                _money(remessa['valor_total'])
                - valor_glosado
                - valor_impostos
            )
            remessa_conciliada = ConciliacaoFaturamentoRemessa(
                conciliacao_id=conciliacao.id,
                cd_remessa=item.cd_remessa,
                convenio=remessa['convenio'],
                cnpj_convenio=remessa['cnpj_convenio'],
                valor_total=_money(remessa['valor_total']),
                sn_glosado=(
                    'true'
                    if item.sn_glosado
                    or (
                        tp_conciliacao != 'recurso'
                        and item.cd_remessa in recursos_abertos
                    )
                    else 'not'
                ),
                valor_glosado=valor_glosado,
                tp_conciliacao=tp_conciliacao,
                valor_alocado_nfse=valor_alocado,
                valor_impostos=valor_impostos,
            )
            session_postgres.add(remessa_conciliada)
            session_postgres.flush()
            vinculos_criados.append(remessa_conciliada)
            if item.sn_glosado:
                _registrar_itens_glosa_conciliacao(
                    session_postgres,
                    conciliacao,
                    remessa_conciliada,
                    itens_glosa_por_remessa[item.cd_remessa],
                )
            remessa_financeira = _obter_ou_criar_remessa_financeira(
                session_postgres,
                remessa,
            )
            valor_recebido = valor_alocado
            if payload.data_recebimento is not None and valor_recebido > 0:
                if payload.conta_bancaria_id is None:
                    raise HTTPException(
                        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                        detail=(
                            'Selecione a conta bancaria para registrar o '
                            'recebimento.'
                        ),
                    )
                recebimento_criado, _ = _registrar_recebimento_remessa(
                    session=session_postgres,
                    remessa=remessa_financeira,
                    conciliacao_id=conciliacao.id,
                    numero_nfse=conciliacao.numero_nfse,
                    data_recebimento=payload.data_recebimento,
                    valor_recebido=valor_recebido,
                    usuario_id=usuario_atual.id,
                    conta_bancaria_id=payload.conta_bancaria_id,
                    conta_plano_contas=payload.conta_plano_contas,
                    conta_centro_custo=payload.conta_centro_custo,
                    lancamento_extrato_id=payload.lancamento_extrato_id,
                )
                recebimentos_criados.append(recebimento_criado)
        _registrar_auditoria_conciliacao(
            session_postgres,
            conciliacao.id,
            'criacao',
            usuario_atual.id,
            dados_novos=_snapshot_conciliacao(
                conciliacao,
                vinculos_criados,
            ),
        )
        for recebimento_criado in recebimentos_criados:
            _registrar_auditoria_conciliacao(
                session_postgres,
                conciliacao.id,
                'recebimento',
                usuario_atual.id,
                dados_novos=_snapshot_conciliacao(
                    conciliacao,
                    vinculos_criados,
                    recebimento_criado,
                ),
            )
        if lancamento is not None:
            lancamento.conciliado = True
        session_postgres.commit()
        session_postgres.refresh(conciliacao)
    except HTTPException:
        session_postgres.rollback()
        raise
    except IntegrityError as exc:
        session_postgres.rollback()
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='A NFS-e ou uma das remessas ja foi conciliada.',
        ) from exc

    return {
        'id': conciliacao.id,
        'nfse_row_hash': conciliacao.nfse_row_hash,
        'numero_nfse': conciliacao.numero_nfse,
        'processo_recebimento': conciliacao.processo_recebimento,
        'valor_nfse': valor_nfse,
        'total_remessas': total_remessas.quantize(CENTAVOS),
        'total_glosas': total_glosas.quantize(CENTAVOS),
        'message': 'Conciliação realizada com sucesso.',
    }
