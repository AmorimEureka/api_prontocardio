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
    ConciliacaoFaturamento,
    ConciliacaoFaturamentoRemessa,
    LancamentoExtratoBancario,
    ModelContaAtendimento,
    ModelGruPro,
    ModelHpcContaBancaria,
    ModelHpcConvenio,
    ModelProFat,
    NfseXml,
    RecebimentoRemessa,
    RegistroGlosa,
    RemessaFinanceira,
    TipoAtendimento,
    Usuario,
)
from app_prontocardio.schema import (
    ConciliacaoFaturamentoCreate,
    ConciliacaoFaturamentoPublic,
    ConciliacoesSemRecebimentoList,
    ContasBancariasRecebimentoList,
    FollowUpGlosasList,
    LancamentosExtratoBancarioList,
    NfsesPendentesConciliacao,
    RecebimentoRemessaCreate,
    RecebimentoRemessaPublic,
    RecebimentosRemessaList,
    RemessasConciliacaoList,
)
from app_prontocardio.security import valida_token_usuario_atual

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
                nota.outras_retencoes,
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
            ModelContaAtendimento.vl_total_conta.label('valor_conta'),
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
            func.sum(func.coalesce(contas_distintas.c.valor_conta, 0)).label(
                'valor_total'
            ),
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
    query = select(ConciliacaoFaturamentoRemessa.cd_remessa).distinct()
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
            func.sum(ConciliacaoFaturamentoRemessa.valor_total).label(
                'valor_consumido'
            ),
        )
        .where(
            ConciliacaoFaturamentoRemessa.tp_conciliacao == 'recurso',
            ConciliacaoFaturamentoRemessa.cd_remessa.in_(totais),
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
    payload: ConciliacaoFaturamentoCreate | RecebimentoRemessaCreate,
    session_postgres: Session,
    session_oracle: Session,
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
        or lancamento.conciliado
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
        )
        remessa_financeira.data_registro = datetime.now(
            ZoneInfo('America/Sao_Paulo')
        ).replace(tzinfo=None)
        session.add(remessa_financeira)
        session.flush()
        return remessa_financeira

    remessa_financeira.convenio = remessa['convenio']
    remessa_financeira.cnpj_convenio = remessa['cnpj_convenio']
    if valor_total > _money(remessa_financeira.valor_total):
        remessa_financeira.valor_total = valor_total
        remessa_financeira.recebimento_integral = (
            _total_recebido_remessa(session, cd_remessa) == valor_total
        )
    return remessa_financeira


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
def consultar_lancamentos_extrato(
    usuario_atual: ValidaUsuarioAtual,
    session: SessionPostgres,
    conta_bancaria_id: int = Query(gt=0),
    data_recebimento: date | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    query = (
        select(LancamentoExtratoBancario)
        .where(
            LancamentoExtratoBancario.conta_bancaria_id == conta_bancaria_id,
            LancamentoExtratoBancario.conciliado.is_(False),
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


def _item_follow_up_glosa(registro: RegistroGlosa) -> dict:
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
                'itens': [],
            }
        pacientes[chave]['itens'].append(_item_follow_up_glosa(registro))
    return list(pacientes.values())


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
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    _sincronizar_itens_follow_up(session, session_oracle)
    pendencia = (
        select(RegistroGlosa.id)
        .where(
            RegistroGlosa.conciliacao_remessa_id
            == ConciliacaoFaturamentoRemessa.id,
            RegistroGlosa.sn_ativo == 'true',
            RegistroGlosa.sn_glosado == 'true',
            RegistroGlosa.valor_recursado.is_(None),
            RegistroGlosa.processo_recurso.is_(None),
            RegistroGlosa.dt_recurso.is_(None),
        )
        .exists()
    )
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
    filtros = [
        ConciliacaoFaturamentoRemessa.sn_glosado == 'true',
        ConciliacaoFaturamentoRemessa.valor_glosado > 0,
        pendencia,
    ]
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

    consulta_base = (
        select(
            ConciliacaoFaturamentoRemessa,
            ConciliacaoFaturamento,
            data_entrega.label('data_entrega'),
            valor_pendente.label('valor_pendente'),
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
    total, valor_total_glosado, valor_total_pendente = session.execute(
        select(
            func.count(),
            func.coalesce(
                func.sum(ConciliacaoFaturamentoRemessa.valor_glosado),
                0,
            ),
            func.coalesce(func.sum(valor_pendente), 0),
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
    ids_vinculos = {row[0].id for row in rows}
    registros_por_vinculo: dict[int, list[RegistroGlosa]] = {
        vinculo_id: [] for vinculo_id in ids_vinculos
    }
    if ids_vinculos:
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
    for vinculo, conciliacao, entrega, pendente in rows:
        cards.append(
            {
                'conciliacao_remessa_id': vinculo.id,
                'cd_remessa': vinculo.cd_remessa,
                'convenio': vinculo.convenio,
                'data_entrega': entrega or conciliacao.data_criacao.date(),
                'numero_nfse': conciliacao.numero_nfse,
                'valor_remessa': _money(vinculo.valor_total),
                'valor_glosado': _money(vinculo.valor_glosado),
                'valor_glosa_pendente': max(
                    _money(pendente),
                    Decimal('0.00'),
                ),
                'pacientes': _pacientes_follow_up_glosa(
                    registros_por_vinculo[vinculo.id]
                ),
            }
        )
    return {
        'cards': cards,
        'total': int(total),
        'valor_total_glosado': _money(valor_total_glosado),
        'valor_total_pendente': _money(valor_total_pendente),
        'limit': limit,
        'offset': offset,
    }


@router.get(
    '/conciliacao-faturamento/sem-recebimento',
    status_code=HTTPStatus.OK,
    response_model=ConciliacoesSemRecebimentoList,
)
def consultar_conciliacoes_sem_recebimento(
    usuario_atual: ValidaUsuarioAtual,
    session: SessionPostgres,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    valor_pendente = (
        ConciliacaoFaturamentoRemessa.valor_total
        - ConciliacaoFaturamentoRemessa.valor_glosado
    )
    remessas_pendentes = (
        select(
            ConciliacaoFaturamentoRemessa.conciliacao_id.label(
                'conciliacao_id'
            ),
            func.count(ConciliacaoFaturamentoRemessa.id).label(
                'quantidade_remessas'
            ),
            func.sum(valor_pendente).label('valor_pendente'),
        )
        .outerjoin(
            RecebimentoRemessa,
            and_(
                RecebimentoRemessa.conciliacao_id
                == ConciliacaoFaturamentoRemessa.conciliacao_id,
                RecebimentoRemessa.cd_remessa
                == ConciliacaoFaturamentoRemessa.cd_remessa,
            ),
        )
        .where(
            RecebimentoRemessa.id.is_(None),
            valor_pendente > 0,
        )
        .group_by(ConciliacaoFaturamentoRemessa.conciliacao_id)
        .subquery()
    )

    filters = []
    termo = (q or '').strip()
    if termo:
        pattern = f'%{termo}%'
        remessa_correspondente = (
            select(ConciliacaoFaturamentoRemessa.id)
            .outerjoin(
                RecebimentoRemessa,
                and_(
                    RecebimentoRemessa.conciliacao_id
                    == ConciliacaoFaturamentoRemessa.conciliacao_id,
                    RecebimentoRemessa.cd_remessa
                    == ConciliacaoFaturamentoRemessa.cd_remessa,
                ),
            )
            .where(
                ConciliacaoFaturamentoRemessa.conciliacao_id
                == ConciliacaoFaturamento.id,
                cast(ConciliacaoFaturamentoRemessa.cd_remessa, String).ilike(
                    pattern
                ),
                RecebimentoRemessa.id.is_(None),
                (
                    ConciliacaoFaturamentoRemessa.valor_total
                    - ConciliacaoFaturamentoRemessa.valor_glosado
                )
                > 0,
            )
            .exists()
        )
        filters.append(
            or_(
                ConciliacaoFaturamento.numero_nfse.ilike(pattern),
                ConciliacaoFaturamento.convenio.ilike(pattern),
                ConciliacaoFaturamento.processo_recebimento.ilike(pattern),
                remessa_correspondente,
            )
        )

    summary_query = (
        select(
            func.count(ConciliacaoFaturamento.id),
            func.coalesce(
                func.sum(remessas_pendentes.c.quantidade_remessas),
                0,
            ),
            func.coalesce(func.sum(remessas_pendentes.c.valor_pendente), 0),
        )
        .join(
            remessas_pendentes,
            remessas_pendentes.c.conciliacao_id
            == ConciliacaoFaturamento.id,
        )
        .where(*filters)
    )
    total, total_remessas_pendentes, valor_total_pendente = session.execute(
        summary_query
    ).one()

    rows = session.execute(
        select(
            ConciliacaoFaturamento,
            remessas_pendentes.c.quantidade_remessas,
            remessas_pendentes.c.valor_pendente,
        )
        .join(
            remessas_pendentes,
            remessas_pendentes.c.conciliacao_id
            == ConciliacaoFaturamento.id,
        )
        .where(*filters)
        .order_by(
            ConciliacaoFaturamento.data_previsao_recebimento,
            ConciliacaoFaturamento.id,
        )
        .offset(offset)
        .limit(limit)
    ).all()
    ids_conciliacao = {row[0].id for row in rows}
    vinculos_por_conciliacao: dict[int, list[tuple]] = {
        conciliacao_id: [] for conciliacao_id in ids_conciliacao
    }
    if ids_conciliacao:
        vinculos = session.execute(
            select(
                ConciliacaoFaturamentoRemessa,
                RecebimentoRemessa,
            )
            .outerjoin(
                RecebimentoRemessa,
                and_(
                    RecebimentoRemessa.conciliacao_id
                    == ConciliacaoFaturamentoRemessa.conciliacao_id,
                    RecebimentoRemessa.cd_remessa
                    == ConciliacaoFaturamentoRemessa.cd_remessa,
                ),
            )
            .where(
                ConciliacaoFaturamentoRemessa.conciliacao_id.in_(
                    ids_conciliacao
                )
            )
            .order_by(
                ConciliacaoFaturamentoRemessa.conciliacao_id,
                ConciliacaoFaturamentoRemessa.cd_remessa,
            )
        ).all()
        for remessa, recebimento in vinculos:
            vinculos_por_conciliacao[remessa.conciliacao_id].append(
                (remessa, recebimento)
            )

    hoje = datetime.now(ZoneInfo('America/Sao_Paulo')).date()
    conciliacoes = []
    for conciliacao, quantidade_pendente, valor_pendente_total in rows:
        vinculos = vinculos_por_conciliacao[conciliacao.id]
        remessas_sem_recebimento = []
        valor_total_remessas = Decimal('0.00')
        valor_total_glosas = Decimal('0.00')
        valor_previsto_recebimento = Decimal('0.00')
        valor_recebido = Decimal('0.00')
        for remessa, recebimento in vinculos:
            valor_remessa = _money(remessa.valor_total)
            valor_glosado = _money(remessa.valor_glosado)
            previsto_remessa = max(
                valor_remessa - valor_glosado,
                Decimal('0.00'),
            )
            valor_total_remessas += valor_remessa
            valor_total_glosas += valor_glosado
            valor_previsto_recebimento += previsto_remessa
            if recebimento is not None:
                valor_recebido += _money(recebimento.valor_recebido)
            elif previsto_remessa > 0:
                remessas_sem_recebimento.append(
                    {
                        'cd_remessa': remessa.cd_remessa,
                        'tp_conciliacao': remessa.tp_conciliacao,
                        'valor_remessa': valor_remessa,
                        'valor_glosado': valor_glosado,
                        'valor_pendente': previsto_remessa,
                    }
                )

        dias_em_atraso = max(
            (hoje - conciliacao.data_previsao_recebimento).days,
            0,
        )
        conciliacoes.append(
            {
                'id': conciliacao.id,
                'numero_nfse': conciliacao.numero_nfse,
                'convenio': conciliacao.convenio,
                'cnpj_convenio': conciliacao.cnpj_convenio,
                'processo_recebimento': conciliacao.processo_recebimento,
                'data_previsao_recebimento': (
                    conciliacao.data_previsao_recebimento
                ),
                'data_criacao': conciliacao.data_criacao,
                'valor_nfse': _money(conciliacao.valor_nfse),
                'quantidade_remessas': len(vinculos),
                'quantidade_remessas_sem_recebimento': int(
                    quantidade_pendente
                ),
                'valor_total_remessas': _money(valor_total_remessas),
                'valor_total_glosas': _money(valor_total_glosas),
                'valor_previsto_recebimento': _money(
                    valor_previsto_recebimento
                ),
                'valor_recebido': _money(valor_recebido),
                'valor_pendente': _money(valor_pendente_total),
                'situacao': (
                    'sem_recebimento'
                    if valor_recebido == 0
                    else 'recebimento_parcial'
                ),
                'em_atraso': dias_em_atraso > 0,
                'dias_em_atraso': dias_em_atraso,
                'remessas': remessas_sem_recebimento,
            }
        )

    return {
        'conciliacoes': conciliacoes,
        'total': int(total),
        'total_remessas_sem_recebimento': int(total_remessas_pendentes),
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
    vinculo = session_postgres.execute(
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
        )
    ).one_or_none()
    if vinculo is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='A NFS-e nao esta vinculada a remessa informada.',
        )

    conciliacao, remessa_conciliada = vinculo
    valor_esperado = _money(
        remessa_conciliada.valor_total - remessa_conciliada.valor_glosado
    )
    if _money(payload.valor_recebido) != valor_esperado:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=(
                f'O recebimento da remessa {payload.cd_remessa} deve ser '
                f'exatamente {_valor_reais_mensagem(valor_esperado)} para '
                'esta NFS-e.'
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
            detail='Ja existe recebimento desta remessa para a NFS-e.',
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


@router.post(
    '/conciliacao-faturamento',
    status_code=HTTPStatus.CREATED,
    response_model=ConciliacaoFaturamentoPublic,
)
def conciliar_faturamento(
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

    valor_nfse = _money(nota.valor_liquido_nfse)
    if (total_remessas - total_glosas).quantize(CENTAVOS) != valor_nfse:
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
            )
            session_postgres.add(remessa_conciliada)
            session_postgres.flush()
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
            valor_recebido = _money(remessa['valor_total']) - valor_glosado
            if payload.data_recebimento is not None and valor_recebido > 0:
                if payload.conta_bancaria_id is None:
                    raise HTTPException(
                        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                        detail=(
                            'Selecione a conta bancaria para registrar o '
                            'recebimento.'
                        ),
                    )
                _registrar_recebimento_remessa(
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
