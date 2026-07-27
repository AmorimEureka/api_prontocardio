import argparse
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app_prontocardio.database import postgres_engine
from app_prontocardio.models import (
    AuditoriaConciliacaoFaturamento,
    ConciliacaoFaturamento,
    ConciliacaoFaturamentoRemessa,
    NfseXml,
    ProcessoConciliacaoRemessa,
    RecebimentoRemessa,
    RegistroGlosa,
    RemessaFinanceira,
)
from app_prontocardio.routers.financeiro import _money, _nota_publica
from app_prontocardio.services.importacao_conciliacoes import (
    LinhaConciliacaoPlanilha,
    ler_linhas_conciliacao_planilha,
)
from app_prontocardio.settings import Settings

CENTAVOS = Decimal('0.01')
HOST_PRODUCAO = '187.127.6.75'


def _argumentos():
    parser = argparse.ArgumentParser(
        description=(
            'Mantém no desenvolvimento apenas o conjunto de conciliações, '
            'glosas e XMLs originado da planilha consolidada.'
        )
    )
    parser.add_argument('planilha', type=Path)
    parser.add_argument('--aba', default='BASE')
    parser.add_argument('--aplicar', action='store_true')
    parser.add_argument(
        '--confirmar-desenvolvimento',
        action='store_true',
        help='Confirma que a conexão configurada pertence ao desenvolvimento.',
    )
    return parser.parse_args()


def _chave_planilha(
    linha: LinhaConciliacaoPlanilha,
) -> tuple[str, str, int]:
    return (
        linha.numero_nfse,
        linha.processo_recebimento,
        linha.cd_remessa,
    )


def _chave_vinculo(
    conciliacao: ConciliacaoFaturamento,
    vinculo: ConciliacaoFaturamentoRemessa,
) -> tuple[str, str, int]:
    return (
        str(conciliacao.numero_nfse).strip(),
        str(conciliacao.processo_recebimento).strip(),
        int(vinculo.cd_remessa),
    )


def _valores_linha(
    linha: LinhaConciliacaoPlanilha,
) -> tuple[Decimal, Decimal, Decimal]:
    return (
        _money(linha.valor_alocado),
        _money(linha.valor_glosado),
        _money(linha.valor_impostos),
    )


def _resolver_linha(
    linhas: list[LinhaConciliacaoPlanilha],
    vinculo: ConciliacaoFaturamentoRemessa,
) -> LinhaConciliacaoPlanilha:
    variantes = {}
    for linha in linhas:
        variantes.setdefault(_valores_linha(linha), linha)
    if len(variantes) == 1:
        return next(iter(variantes.values()))

    valores_atuais = (
        _money(vinculo.valor_alocado_nfse),
        _money(vinculo.valor_glosado),
    )
    correspondentes = [
        linha
        for linha in variantes.values()
        if _valores_linha(linha)[:2] == valores_atuais
    ]
    retencoes = {
        _money(linha.valor_impostos) for linha in correspondentes
    }
    if len(correspondentes) >= 1 and len(retencoes) == 1:
        return correspondentes[0]
    raise RuntimeError(
        'A planilha possui valores ambíguos para a chave '
        f'{_chave_planilha(linhas[0])}.'
    )


def _validar_ambiente(args) -> None:
    if postgres_engine is None:
        raise RuntimeError('DATABASE_URL não configurada.')
    host = urlparse(str(Settings().DATABASE_URL)).hostname
    print(f'Banco de destino: host={host or "-"}')
    if host == HOST_PRODUCAO:
        raise RuntimeError(
            'Este utilitário não pode ser executado no banco de produção.'
        )
    if args.aplicar and not args.confirmar_desenvolvimento:
        raise RuntimeError(
            'Use --confirmar-desenvolvimento para autorizar a limpeza.'
        )


def _ids_recebimentos_fora_dataset(
    recebimentos: list[RecebimentoRemessa],
    pares_validos: set[tuple[int, int]],
) -> list[int]:
    return [
        recebimento.id
        for recebimento in recebimentos
        if (recebimento.conciliacao_id, recebimento.cd_remessa)
        not in pares_validos
    ]


def main() -> None:  # noqa: PLR0912, PLR0915
    args = _argumentos()
    _validar_ambiente(args)
    linhas = ler_linhas_conciliacao_planilha(args.planilha, args.aba)
    linhas_por_chave: dict[
        tuple[str, str, int],
        list[LinhaConciliacaoPlanilha],
    ] = defaultdict(list)
    for linha in linhas:
        linhas_por_chave[_chave_planilha(linha)].append(linha)

    with Session(postgres_engine) as session:
        rows = session.execute(
            select(
                ConciliacaoFaturamento,
                ConciliacaoFaturamentoRemessa,
            )
            .join(
                ConciliacaoFaturamentoRemessa,
                ConciliacaoFaturamentoRemessa.conciliacao_id
                == ConciliacaoFaturamento.id,
            )
            .order_by(
                ConciliacaoFaturamento.id,
                ConciliacaoFaturamentoRemessa.id,
            )
        ).all()
        validos = []
        invalidos = []
        for conciliacao, vinculo in rows:
            linhas_chave = linhas_por_chave.get(
                _chave_vinculo(conciliacao, vinculo)
            )
            if linhas_chave:
                validos.append(
                    (
                        conciliacao,
                        vinculo,
                        _resolver_linha(linhas_chave, vinculo),
                    )
                )
            else:
                invalidos.append((conciliacao, vinculo))

        ids_vinculos_validos = {
            vinculo.id for _, vinculo, _ in validos
        }
        ids_conciliacoes_validas = {
            conciliacao.id for conciliacao, _, _ in validos
        }
        ids_processos_validos = {
            vinculo.processo_remessa_id
            for _, vinculo, _ in validos
            if vinculo.processo_remessa_id is not None
        }
        codigos_remessas_validas = {
            vinculo.cd_remessa for _, vinculo, _ in validos
        }
        pares_validos = {
            (vinculo.conciliacao_id, vinculo.cd_remessa)
            for _, vinculo, _ in validos
        }
        numeros_nfse_planilha = {
            linha.numero_nfse for linha in linhas
        }

        hashes = {
            conciliacao.nfse_row_hash for conciliacao, _, _ in validos
        }
        nfses = {
            nota.row_hash: nota
            for nota in session.scalars(
                select(NfseXml).where(NfseXml.row_hash.in_(hashes))
            )
        }
        hashes_ausentes = sorted(hashes - nfses.keys())
        if hashes_ausentes:
            raise RuntimeError(
                f'{len(hashes_ausentes)} XML(s) referenciado(s) não '
                'foram encontrados.'
            )

        retencoes_por_nfse = defaultdict(lambda: Decimal('0.00'))
        limites_por_nfse = {}
        correcoes_valores = 0
        total_retencoes = Decimal('0.00')
        for conciliacao, vinculo, linha in validos:
            valor_alocado, valor_glosado, valor_retencoes = (
                _valores_linha(linha)
            )
            if (
                _money(vinculo.valor_alocado_nfse) != valor_alocado
                or _money(vinculo.valor_glosado) != valor_glosado
            ):
                correcoes_valores += 1
            total_retencoes += valor_retencoes
            chave_nfse = (
                str(conciliacao.numero_nfse).strip(),
                str(conciliacao.cnpj_convenio).strip(),
            )
            retencoes_por_nfse[chave_nfse] += valor_retencoes
            total_nfse = _money(
                _nota_publica(nfses[conciliacao.nfse_row_hash])[
                    'impostos'
                ]
            )
            limites_por_nfse[chave_nfse] = max(
                limites_por_nfse.get(chave_nfse, Decimal('0.00')),
                total_nfse,
            )

        excedentes = sorted(
            (
                chave,
                valor,
                limites_por_nfse.get(chave, Decimal('0.00')),
            )
            for chave, valor in retencoes_por_nfse.items()
            if valor > limites_por_nfse.get(chave, Decimal('0.00'))
        )
        if excedentes:
            for chave, valor, limite in excedentes[:20]:
                print(
                    'RETENCOES_AJUSTADAS_PELA_PLANILHA',
                    f'NFS-e={chave[0]}',
                    f'planilha=R$ {valor}',
                    f'xml=R$ {limite}',
                )
            for chave, valor, _ in excedentes:
                limites_por_nfse[chave] = valor

        glosas_fora = session.scalars(
            select(RegistroGlosa.id).where(
                or_(
                    RegistroGlosa.conciliacao_remessa_id.is_(None),
                    RegistroGlosa.conciliacao_remessa_id.not_in(
                        ids_vinculos_validos
                    ),
                )
            )
        ).all()
        recebimentos = list(session.scalars(select(RecebimentoRemessa)))
        recebimentos_fora = _ids_recebimentos_fora_dataset(
            recebimentos,
            pares_validos,
        )
        nfses_fora = session.scalar(
            select(func.count())
            .select_from(NfseXml)
            .where(
                or_(
                    NfseXml.numero_nfse.is_(None),
                    NfseXml.numero_nfse.not_in(numeros_nfse_planilha),
                )
            )
        )

        print(f'Linhas válidas da planilha: {len(linhas)}')
        print(f'Vínculos mantidos: {len(validos)}')
        print(f'Vínculos removidos: {len(invalidos)}')
        print(f'Glosas removidas: {len(glosas_fora)}')
        print(f'Recebimentos removidos: {len(recebimentos_fora)}')
        print(f'XMLs removidos: {int(nfses_fora or 0)}')
        print(f'Vínculos com valores corrigidos: {correcoes_valores}')
        print(
            'NFS-e com total de retenções ajustado pela planilha: '
            f'{len(excedentes)}'
        )
        print(f'Total das retenções distribuídas: R$ {total_retencoes}')

        if not args.aplicar:
            session.rollback()
            print('Simulação concluída; nenhuma alteração foi gravada.')
            return

        for conciliacao, vinculo, linha in validos:
            valor_alocado, valor_glosado, valor_retencoes = (
                _valores_linha(linha)
            )
            vinculo.valor_alocado_nfse = valor_alocado
            vinculo.valor_glosado = valor_glosado
            vinculo.valor_impostos = valor_retencoes
            vinculo.valor_total = (
                valor_alocado + valor_glosado + valor_retencoes
            )
            vinculo.sn_glosado = (
                'true' if valor_glosado > 0 else 'not'
            )
            chave_nfse = (
                str(conciliacao.numero_nfse).strip(),
                str(conciliacao.cnpj_convenio).strip(),
            )
            conciliacao.impostos = limites_por_nfse[chave_nfse]

        if glosas_fora:
            session.execute(
                delete(RegistroGlosa).where(
                    RegistroGlosa.id.in_(glosas_fora)
                )
            )
        if recebimentos_fora:
            session.execute(
                delete(RecebimentoRemessa).where(
                    RecebimentoRemessa.id.in_(recebimentos_fora)
                )
            )
        session.execute(
            delete(AuditoriaConciliacaoFaturamento).where(
                AuditoriaConciliacaoFaturamento.conciliacao_id.not_in(
                    ids_conciliacoes_validas
                )
            )
        )
        session.execute(
            delete(ConciliacaoFaturamento).where(
                ConciliacaoFaturamento.id.not_in(
                    ids_conciliacoes_validas
                )
            )
        )
        session.execute(
            delete(ProcessoConciliacaoRemessa).where(
                ProcessoConciliacaoRemessa.id.not_in(
                    ids_processos_validos
                )
            )
        )
        session.execute(
            delete(RemessaFinanceira).where(
                RemessaFinanceira.cd_remessa.not_in(
                    codigos_remessas_validas
                )
            )
        )
        session.execute(
            delete(NfseXml).where(
                or_(
                    NfseXml.numero_nfse.is_(None),
                    NfseXml.numero_nfse.not_in(numeros_nfse_planilha),
                )
            )
        )
        session.commit()
        print('Dataset da planilha preparado com sucesso.')


if __name__ == '__main__':
    main()
