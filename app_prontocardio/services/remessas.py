from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app_prontocardio.models import (
    ModelContaAtendimento,
    RecebimentoRemessa,
    RemessaFinanceira,
)

CENTAVOS = Decimal('0.01')
ORACLE_IN_CHUNK_SIZE = 900


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(CENTAVOS, rounding=ROUND_HALF_UP)


def consultar_totais_remessas_hpc(
    session_oracle: Session,
    cd_remessas: set[int],
) -> dict[int, Decimal]:
    totais: dict[int, Decimal] = {}
    codigos = sorted(cd_remessas)
    for offset in range(0, len(codigos), ORACLE_IN_CHUNK_SIZE):
        chunk = codigos[offset : offset + ORACLE_IN_CHUNK_SIZE]
        registros = (
            select(
                ModelContaAtendimento.cd_remessa.label('cd_remessa'),
                ModelContaAtendimento.cd_reg.label('cd_reg'),
                ModelContaAtendimento.vl_total_registro.label(
                    'valor_registro'
                ),
            )
            .where(ModelContaAtendimento.cd_remessa.in_(chunk))
            .distinct()
            .subquery()
        )
        rows = session_oracle.execute(
            select(
                registros.c.cd_remessa,
                func.sum(
                    func.coalesce(registros.c.valor_registro, 0)
                ).label('valor_total'),
            ).group_by(registros.c.cd_remessa)
        ).all()
        totais.update(
            {
                int(row.cd_remessa): _money(row.valor_total)
                for row in rows
                if row.cd_remessa is not None
            }
        )
    return totais


def _atualizar_estado_recebimentos(
    remessa: RemessaFinanceira,
    itens: list[RecebimentoRemessa],
    novo_total: Decimal,
) -> bool:
    alterado = False
    valor_recebido = sum(
        (_money(item.valor_recebido) for item in itens),
        Decimal('0.00'),
    )
    recebimento_integral = bool(itens) and valor_recebido >= novo_total
    if remessa.recebimento_integral != recebimento_integral:
        remessa.recebimento_integral = recebimento_integral
        alterado = True

    ultimo_recebimento = itens[-1] if itens else None
    for item in itens:
        item_integral = recebimento_integral and item is ultimo_recebimento
        if item.recebimento_integral != item_integral:
            item.recebimento_integral = item_integral
            alterado = True
    return alterado


def sincronizar_totais_remessas_financeiras(
    session_postgres: Session,
    session_oracle: Session,
    cd_remessas: set[int] | None = None,
    *,
    commit: bool = True,
) -> dict[int, Decimal]:
    if not hasattr(session_oracle, 'execute'):
        return {}

    query = select(RemessaFinanceira)
    if cd_remessas is not None:
        if not cd_remessas:
            return {}
        query = query.where(RemessaFinanceira.cd_remessa.in_(cd_remessas))
    remessas = list(session_postgres.scalars(query))
    codigos = (
        set(cd_remessas)
        if cd_remessas is not None
        else {remessa.cd_remessa for remessa in remessas}
    )
    if not codigos:
        return {}

    totais_hpc = consultar_totais_remessas_hpc(session_oracle, codigos)
    if not totais_hpc:
        return {}
    if not remessas:
        return totais_hpc

    recebimentos = list(
        session_postgres.scalars(
            select(RecebimentoRemessa)
            .where(RecebimentoRemessa.cd_remessa.in_(totais_hpc))
            .order_by(
                RecebimentoRemessa.cd_remessa,
                RecebimentoRemessa.data_recebimento,
                RecebimentoRemessa.id,
            )
        )
    )
    recebimentos_por_remessa: dict[int, list[RecebimentoRemessa]] = {
        codigo: [] for codigo in totais_hpc
    }
    for recebimento in recebimentos:
        recebimentos_por_remessa[recebimento.cd_remessa].append(recebimento)

    alterado = False
    for remessa in remessas:
        novo_total = totais_hpc.get(remessa.cd_remessa)
        if novo_total is None:
            continue
        if _money(remessa.valor_total) != novo_total:
            remessa.valor_total = novo_total
            alterado = True

        itens = recebimentos_por_remessa[remessa.cd_remessa]
        alterado = (
            _atualizar_estado_recebimentos(remessa, itens, novo_total)
            or alterado
        )

    if alterado:
        session_postgres.flush()
        if commit:
            session_postgres.commit()
    return totais_hpc
