from datetime import date
from decimal import Decimal

from openpyxl import Workbook

from app_prontocardio.services.importacao_conciliacoes import (
    LinhaConciliacaoPlanilha,
    VinculoConciliacaoAtual,
    ler_linhas_conciliacao_planilha,
    planejar_reprocessamento_conciliacoes,
)


def vinculo_atual(**alteracoes):
    dados = {
        'conciliacao_id': 1,
        'vinculo_id': 1,
        'nfse_row_hash': 'hash-nfse',
        'numero_nfse': '27951',
        'processo_recebimento': 'P151391/2026',
        'cnpj_convenio': '07965184000173',
        'valor_nfse': Decimal('210351.10'),
        'cd_remessa': 17500,
        'valor_alocado': Decimal('24985.66'),
        'valor_glosado': Decimal('1.35'),
        'data_previsao_recebimento': date(2026, 6, 16),
        'usuario_id': 4,
    }
    for campo in ('valor_nfse', 'valor_alocado', 'valor_glosado'):
        if campo in alteracoes:
            alteracoes[campo] = Decimal(alteracoes[campo])
    dados.update(alteracoes)
    return VinculoConciliacaoAtual(
        conciliacao_id=dados['conciliacao_id'],
        vinculo_id=dados['vinculo_id'],
        nfse_row_hash='hash-nfse',
        numero_nfse=dados['numero_nfse'],
        processo_recebimento=dados['processo_recebimento'],
        cnpj_convenio='07965184000173',
        valor_nfse=dados['valor_nfse'],
        cd_remessa=dados['cd_remessa'],
        valor_alocado=dados['valor_alocado'],
        valor_glosado=dados['valor_glosado'],
        data_previsao_recebimento=date(2026, 6, 16),
        usuario_id=4,
    )


def linha_planilha(
    *,
    numero_linha,
    cd_remessa,
    valor_alocado,
    valor_glosado='1.35',
):
    return LinhaConciliacaoPlanilha(
        numero_linha=numero_linha,
        numero_nfse='27951',
        processo_recebimento='P151391/2026',
        cd_remessa=cd_remessa,
        valor_alocado=Decimal(valor_alocado),
        valor_glosado=Decimal(valor_glosado),
        data_previsao_recebimento=date(2026, 6, 16),
    )


def test_planeja_todas_as_remessas_repetidas_sem_descartar_nfse():
    linhas = [
        linha_planilha(
            numero_linha=10,
            cd_remessa=17413,
            valor_alocado='61672.96',
        ),
        linha_planilha(
            numero_linha=11,
            cd_remessa=17500,
            valor_alocado='21790.75',
        ),
        linha_planilha(
            numero_linha=12,
            cd_remessa=17501,
            valor_alocado='17054.07',
        ),
    ]

    plano = planejar_reprocessamento_conciliacoes(
        linhas,
        [vinculo_atual()],
    )

    assert plano.grupos_carga == 1
    assert plano.grupos_analisados == 1
    assert plano.grupos_sem_remessa_numerica == 0
    assert plano.grupos_repetidos == 1
    assert plano.vinculos_esperados == len(linhas)
    assert plano.vinculos_presentes == 1
    assert len(plano.ajustes) == 1
    assert plano.ajustes[0].linha.valor_alocado == Decimal('21790.75')
    assert [item.linha.cd_remessa for item in plano.novos] == [17413, 17501]


def test_limita_ultima_remessa_ao_saldo_compartilhado_da_nfse():
    linhas = [
        linha_planilha(
            numero_linha=10,
            cd_remessa=17500,
            valor_alocado='80.00',
            valor_glosado='0.00',
        ),
        linha_planilha(
            numero_linha=11,
            cd_remessa=17501,
            valor_alocado='30.01',
            valor_glosado='0.00',
        ),
    ]
    atual = vinculo_atual(
        valor_nfse='100.00',
        valor_alocado='80.00',
    )
    atual = VinculoConciliacaoAtual(**{
        **atual.__dict__,
        'valor_glosado': Decimal('0.00'),
    })

    plano = planejar_reprocessamento_conciliacoes(linhas, [atual])

    assert len(plano.ajustes) == 0
    assert len(plano.novos) == 1
    assert plano.novos[0].linha.valor_alocado == Decimal('20.00')


def test_audita_todos_os_grupos_sem_reprocessar_linha_unica():
    linha = linha_planilha(
        numero_linha=10,
        cd_remessa=17500,
        valor_alocado='80.00',
        valor_glosado='0.00',
    )
    atual = vinculo_atual(
        valor_nfse='100.00',
        valor_alocado='80.00',
        valor_glosado='0.00',
    )
    recurso_sem_remessa_numerica = vinculo_atual(
        conciliacao_id=2,
        vinculo_id=2,
        numero_nfse='5367',
        processo_recebimento='228648377',
        cd_remessa=18696,
    )

    vinculos = [atual, recurso_sem_remessa_numerica]
    plano = planejar_reprocessamento_conciliacoes([linha], vinculos)

    assert plano.grupos_carga == len(vinculos)
    assert plano.grupos_analisados == 1
    assert plano.grupos_sem_remessa_numerica == 1
    assert plano.vinculos_esperados == 1
    assert plano.vinculos_presentes == 1
    assert plano.ajustes == ()
    assert plano.novos == ()


def test_le_planilha_preservando_uma_linha_por_remessa(tmp_path):
    arquivo = tmp_path / 'conciliacoes.xlsx'
    workbook = Workbook()
    planilha = workbook.active
    planilha.title = 'BASE'
    planilha.append(['Relatório'])
    planilha.append([])
    planilha.append([
        'Convênio',
        'Coluna1',
        'GLOSA',
        'VLR LIQ NF ',
        'NF ',
        'PREV. RECBTO.',
        'PROCESSO',
    ])
    planilha.append([
        'IPM',
        17413,
        937.91,
        61672.955,
        27951,
        date(2026, 6, 16),
        'P151391/2026',
    ])
    planilha.append([
        'IPM',
        17500,
        1.35,
        21790.754,
        27951,
        date(2026, 6, 16),
        'P151391/2026',
    ])
    workbook.save(arquivo)

    linhas = ler_linhas_conciliacao_planilha(arquivo)

    assert [(item.cd_remessa, item.valor_alocado) for item in linhas] == [
        (17413, Decimal('61672.96')),
        (17500, Decimal('21790.75')),
    ]
