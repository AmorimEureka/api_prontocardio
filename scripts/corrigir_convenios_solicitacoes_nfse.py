import argparse
from collections import Counter
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app_prontocardio.database import oracle_engine, postgres_engine
from app_prontocardio.models import SolicitacaoNota, SolicitacaoNotaEvento
from app_prontocardio.routers import requisicoes


def _argumentos():
    parser = argparse.ArgumentParser(
        description=(
            'Corrige o convênio de solicitações cujo procedimento e valor '
            'correspondem exatamente aos itens Particular/Prontorede.'
        )
    )
    parser.add_argument(
        '--codigo-atendimento',
        type=int,
        action='append',
        default=[],
        help='Limita a correção a um atendimento; pode ser repetido.',
    )
    parser.add_argument('--aplicar', action='store_true')
    return parser.parse_args()


def _linhas_procedimento(value) -> Counter:
    return Counter(
        linha.strip().upper()
        for linha in str(value or '').splitlines()
        if linha.strip()
    )


def _procedimentos_elegiveis(procedimentos):
    return [
        procedimento
        for procedimento in procedimentos
        if procedimento.convenio_elegivel_nfse
    ]


def _corresponde_aos_itens_elegiveis(solicitacao, procedimentos) -> bool:
    elegiveis = _procedimentos_elegiveis(procedimentos)
    descricoes = Counter(
        procedimento.descricao.strip().upper()
        for procedimento in elegiveis
        if procedimento.descricao.strip()
    )
    valor = sum(
        (
            procedimento.valor_total or Decimal('0')
            for procedimento in elegiveis
        ),
        Decimal('0'),
    )
    return bool(descricoes) and (
        _linhas_procedimento(solicitacao.procedimento) == descricoes
        and solicitacao.valor_nota == valor
    )


def main() -> None:
    args = _argumentos()
    if postgres_engine is None:
        raise RuntimeError('DATABASE_URL não configurada.')

    with Session(postgres_engine) as session_postgres:
        query = select(SolicitacaoNota).where(
            SolicitacaoNota.ativo.is_(True)
        )
        if args.codigo_atendimento:
            query = query.where(
                SolicitacaoNota.codigo_atendimento.in_(
                    set(args.codigo_atendimento)
                )
            )
        solicitacoes = list(
            session_postgres.scalars(
                query.order_by(SolicitacaoNota.id)
            ).all()
        )
        codigos_atendimento = {
            solicitacao.codigo_atendimento for solicitacao in solicitacoes
        }
        with Session(oracle_engine) as session_oracle:
            procedimentos_por_atendimento, disponiveis = (
                requisicoes._consultar_procedimentos_atendimentos(
                    codigos_atendimento,
                    session_oracle,
                )
            )
        if not disponiveis:
            raise RuntimeError(
                'Não foi possível consultar os procedimentos no Oracle.'
            )

        correcoes = []
        for solicitacao in solicitacoes:
            procedimentos = procedimentos_por_atendimento.get(
                solicitacao.codigo_atendimento,
                [],
            )
            if not _corresponde_aos_itens_elegiveis(
                solicitacao,
                procedimentos,
            ):
                continue
            codigo_convenio, convenio = (
                requisicoes._dados_convenio_procedimentos_elegiveis_nfse(
                    procedimentos,
                    solicitacao.codigo_convenio,
                    solicitacao.convenio,
                )
            )
            if (
                solicitacao.codigo_convenio == codigo_convenio
                and solicitacao.convenio == convenio
            ):
                continue
            correcoes.append(
                (
                    solicitacao,
                    codigo_convenio,
                    convenio,
                    solicitacao.codigo_convenio,
                    solicitacao.convenio,
                )
            )

        for (
            solicitacao,
            codigo_convenio,
            convenio,
            codigo_anterior,
            convenio_anterior,
        ) in correcoes:
            print(
                f'Solicitação {solicitacao.id} / atendimento '
                f'{solicitacao.codigo_atendimento}: '
                f'{codigo_anterior} - {convenio_anterior} -> '
                f'{codigo_convenio} - {convenio}'
            )
            if not args.aplicar:
                continue
            solicitacao.codigo_convenio = codigo_convenio
            solicitacao.convenio = convenio
            session_postgres.add(
                SolicitacaoNotaEvento(
                    solicitacao_nota_id=solicitacao.id,
                    usuario_id=solicitacao.usuario_id,
                    tipo_acao='CORRECAO_CONVENIO_NFSE',
                    observacao=(
                        f'Convênio corrigido de {codigo_anterior} - '
                        f'{convenio_anterior} para {codigo_convenio} - '
                        f'{convenio}, conforme itens elegíveis da NFS-e.'
                    )[:500],
                )
            )

        if args.aplicar:
            session_postgres.commit()
            print(f'{len(correcoes)} solicitação(ões) corrigida(s).')
        else:
            print(
                f'Simulação concluída: {len(correcoes)} correção(ões) '
                'identificada(s); nenhuma alteração foi gravada.'
            )


if __name__ == '__main__':
    main()
