import argparse
from collections import Counter
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app_prontocardio.database import oracle_engine, postgres_engine
from app_prontocardio.models import (
    ConciliacaoFaturamento,
    ConciliacaoFaturamentoRemessa,
    Usuario,
)
from app_prontocardio.routers import financeiro
from app_prontocardio.schema import (
    ConciliacaoFaturamentoUpdate,
    ConciliacaoRemessaCreate,
)
from app_prontocardio.services.importacao_conciliacoes import (
    VinculoConciliacaoAtual,
    ler_linhas_conciliacao_planilha,
    planejar_reprocessamento_conciliacoes,
)


def _argumentos():
    parser = argparse.ArgumentParser(
        description=(
            'Reprocessa vínculos repetidos entre NFS-e e remessas da '
            'planilha, preservando o saldo fiscal compartilhado.'
        )
    )
    parser.add_argument('planilha', type=Path)
    parser.add_argument('--aba', default='BASE')
    parser.add_argument('--usuario-id', type=int)
    parser.add_argument('--nfse', action='append', default=[])
    parser.add_argument('--aplicar', action='store_true')
    parser.add_argument(
        '--confirmar-desenvolvimento',
        action='store_true',
        help='Confirma que a conexão configurada pertence ao desenvolvimento.',
    )
    return parser.parse_args()


def _carregar_vinculos(session: Session):
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
        .where(ConciliacaoFaturamento.ativo.is_(True))
        .order_by(
            ConciliacaoFaturamento.id,
            ConciliacaoFaturamentoRemessa.id,
        )
    )
    return [
        VinculoConciliacaoAtual(
            conciliacao_id=conciliacao.id,
            vinculo_id=vinculo.id,
            nfse_row_hash=conciliacao.nfse_row_hash,
            numero_nfse=str(conciliacao.numero_nfse).strip(),
            processo_recebimento=str(conciliacao.processo_recebimento).strip(),
            cnpj_convenio=str(conciliacao.cnpj_convenio).strip(),
            valor_nfse=financeiro._money(conciliacao.valor_nfse),
            cd_remessa=vinculo.cd_remessa,
            valor_alocado=financeiro._valor_alocado_vinculo(vinculo),
            valor_glosado=financeiro._money(vinculo.valor_glosado),
            data_previsao_recebimento=(conciliacao.data_previsao_recebimento),
            usuario_id=conciliacao.usuario_id,
        )
        for conciliacao, vinculo in rows
    ]


def _resolver_usuario_id(args, plano) -> int:
    if args.usuario_id is not None:
        return args.usuario_id
    ids = [ajuste.atual.usuario_id for ajuste in plano.ajustes] + [
        novo.referencia.usuario_id for novo in plano.novos
    ]
    if not ids:
        raise RuntimeError('O plano não possui alterações para aplicar.')
    mais_comuns = Counter(ids).most_common()
    if len(mais_comuns) != 1:
        raise RuntimeError(
            'Informe --usuario-id para rastrear a operação de reprocessamento.'
        )
    return mais_comuns[0][0]


def _imprimir_plano(plano) -> None:
    print(f'Agrupamentos da carga atual: {plano.grupos_carga}')
    print(f'Agrupamentos cruzados com a planilha: {plano.grupos_analisados}')
    print(
        'Agrupamentos sem remessa numérica na planilha: '
        f'{plano.grupos_sem_remessa_numerica}'
    )
    print(f'Grupos repetidos encontrados: {plano.grupos_repetidos}')
    print(f'Vínculos esperados: {plano.vinculos_esperados}')
    print(f'Vínculos presentes: {plano.vinculos_presentes}')
    print(f'Vínculos a ajustar: {len(plano.ajustes)}')
    print(f'Vínculos a criar: {len(plano.novos)}')
    for ajuste in plano.ajustes:
        print(
            'AJUSTAR',
            f'NFS-e {ajuste.linha.numero_nfse}',
            f'remessa {ajuste.linha.cd_remessa}',
            f'R$ {ajuste.atual.valor_alocado}',
            '->',
            f'R$ {ajuste.linha.valor_alocado}',
        )
    for novo in plano.novos:
        print(
            'CRIAR',
            f'NFS-e {novo.linha.numero_nfse}',
            f'remessa {novo.linha.cd_remessa}',
            f'R$ {novo.linha.valor_alocado}',
        )


def main() -> None:
    args = _argumentos()
    linhas = ler_linhas_conciliacao_planilha(args.planilha, args.aba)
    nfses = {str(item).strip() for item in args.nfse if str(item).strip()}
    if nfses:
        linhas = [linha for linha in linhas if linha.numero_nfse in nfses]

    if postgres_engine is None:
        raise RuntimeError('DATABASE_URL não configurada.')
    with Session(postgres_engine) as session_postgres:
        vinculos = _carregar_vinculos(session_postgres)
        if nfses:
            vinculos = [
                vinculo for vinculo in vinculos if vinculo.numero_nfse in nfses
            ]
        plano = planejar_reprocessamento_conciliacoes(linhas, vinculos)
        _imprimir_plano(plano)
        if not args.aplicar:
            print('Simulação concluída; nenhuma alteração foi gravada.')
            return
        if not args.confirmar_desenvolvimento:
            raise RuntimeError(
                'Use --confirmar-desenvolvimento para autorizar a gravação.'
            )
        if not plano.ajustes and not plano.novos:
            print('Nenhuma alteração pendente.')
            return

        usuario_id = _resolver_usuario_id(args, plano)
        usuario = session_postgres.get(Usuario, usuario_id)
        if usuario is None:
            raise RuntimeError(f'Usuário {usuario_id} não encontrado.')

        with Session(oracle_engine) as session_oracle:
            for ajuste in plano.ajustes:
                financeiro.editar_conciliacao_faturamento(
                    conciliacao_id=ajuste.atual.conciliacao_id,
                    payload=ConciliacaoFaturamentoUpdate(
                        remessas=[
                            {
                                'cd_remessa': ajuste.linha.cd_remessa,
                                'valor_recebido': (ajuste.linha.valor_alocado),
                                'valor_glosado': ajuste.linha.valor_glosado,
                                'valor_impostos': (
                                    ajuste.linha.valor_impostos
                                ),
                            }
                        ]
                    ),
                    usuario_atual=usuario,
                    session=session_postgres,
                    session_oracle=session_oracle,
                )
            for novo in plano.novos:
                data_previsao = (
                    novo.linha.data_previsao_recebimento
                    or novo.referencia.data_previsao_recebimento
                )
                financeiro.conciliar_remessa_com_nfses(
                    cd_remessa=novo.linha.cd_remessa,
                    payload=ConciliacaoRemessaCreate(
                        processo_recebimento=(novo.linha.processo_recebimento),
                        notas=[
                            {
                                'nfse_row_hash': (
                                    novo.referencia.nfse_row_hash
                                ),
                                'valor_alocado': novo.linha.valor_alocado,
                                'valor_impostos': (
                                    novo.linha.valor_impostos
                                ),
                                'sn_glosado': novo.linha.valor_glosado > 0,
                                'valor_glosado': novo.linha.valor_glosado,
                                'data_previsao_recebimento': data_previsao,
                            }
                        ],
                    ),
                    usuario_atual=usuario,
                    session_postgres=session_postgres,
                    session_oracle=session_oracle,
                )

    print(
        f'Reprocessamento concluído: {len(plano.ajustes)} ajuste(s) e '
        f'{len(plano.novos)} vínculo(s) criado(s).'
    )


if __name__ == '__main__':
    main()
