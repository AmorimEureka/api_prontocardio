from datetime import date
from decimal import Decimal

from app_prontocardio.models import (
    ConciliacaoFaturamento,
    ConciliacaoFaturamentoRemessa,
    RecebimentoRemessa,
    RegistroGlosa,
    RegistroGlosaDemonstrativoIpm,
    RemessaFinanceira,
)
from app_prontocardio.services.importacao_glosas_ipm import (
    ChaveProcesso,
    associar_demonstrativos_a_processos,
    associar_processos_a_remessas,
    chave_item_demonstrativo,
    chave_item_oracle,
    classificar_demonstrativos_sem_processo_por_oracle,
    indexar_processos,
    normalizar_competencia,
    resolver_item,
)
from scripts.importar_glosas_demonstrativo_ipm import (
    ItemGlosaPlano,
    ProcessoPlano,
    RemessaPlano,
    _aplicar_plano,
)

CONTA_TESTE = 100
CONTA_BANCARIA_TESTE = 7


def processo(**alteracoes):
    dados = {
        'numero_processo': 'P001/2026',
        'competencia_producao': '12/2025',
        'valor_protocolo': Decimal('100.00'),
        'nr': 'PROTOCOLO-1',
        'nr_origem': None,
    }
    dados.update(alteracoes)
    return dados


def demonstrativo(**alteracoes):
    dados = {
        'id_registro': 'demo-1',
        'numero_protocolo': 'protocolo-1',
        'valor_protocolo': Decimal('100.00'),
        'numero_guia_senha': 'GUIA-1',
        'codigo_servico': 'SERVICO-1',
        'codigo_beneficiario': '1234567890',
    }
    dados.update(alteracoes)
    return dados


def test_normaliza_competencia_com_ano_de_dois_ou_quatro_digitos():
    assert normalizar_competencia('12/2025') == date(2025, 12, 1)
    assert normalizar_competencia('01/26') == date(2026, 1, 1)
    assert normalizar_competencia('inválida') is None


def test_associa_demonstrativo_por_protocolo_e_valor_exatos():
    indice, _ = indexar_processos([
        processo(),
        processo(
            numero_processo='P002/2026',
            valor_protocolo=Decimal('200.00'),
        ),
    ])

    associacao = associar_demonstrativos_a_processos(
        [
            demonstrativo(),
            demonstrativo(
                id_registro='demo-sem-processo',
                valor_protocolo=Decimal('101.00'),
            ),
        ],
        indice,
    )

    assert associacao.unicas['demo-1'].numero_processo == 'P001/2026'
    assert [item['id_registro'] for item in associacao.sem_processo] == [
        'demo-sem-processo'
    ]
    assert associacao.ambiguas == ()


def test_classifica_associacoes_ambiguas_e_remessas_nao_encontradas():
    primeiro = ChaveProcesso(
        'P001/2026',
        date(2025, 12, 1),
        Decimal('100.00'),
    )
    segundo = ChaveProcesso(
        'P002/2026',
        date(2026, 1, 1),
        Decimal('200.00'),
    )

    associacao = associar_processos_a_remessas(
        [primeiro, segundo],
        {Decimal('100.00'): {10, 11}},
    )

    assert associacao.unicas == {}
    assert associacao.ambiguas == ((primeiro, (10, 11)),)
    assert associacao.nao_encontradas == (segundo,)


def test_associa_processo_a_remessa_sem_usar_competencia():
    processo = ChaveProcesso(
        'P001/2026',
        date(2022, 1, 1),
        Decimal('18450.96'),
    )

    associacao = associar_processos_a_remessas(
        [processo],
        {Decimal('18450.96'): {1000}},
    )

    assert associacao.unicas == {processo: 1000}


def test_chave_do_item_usa_os_dez_ultimos_digitos_da_carteira():
    demo = demonstrativo(codigo_beneficiario='123.456.789-0')
    oracle = {
        'cd_remessa': 10,
        'nr_guia': 'guia-1',
        'cd_pro_fat': 'servico-1',
        'nr_carteira': '0000.123.456.789-0',
    }

    assert chave_item_demonstrativo(demo, 10) == chave_item_oracle(oracle)


def test_resolve_multiplos_lancamentos_mesma_conta_sem_inventar_lancamento():
    resolucao = resolver_item([
        (CONTA_TESTE, 1),
        (CONTA_TESTE, 2),
        (CONTA_TESTE, 2),
    ])

    assert resolucao.status == 'conta_unica'
    assert resolucao.conta == CONTA_TESTE
    assert resolucao.cd_lancamento is None
    assert resolucao.candidatos == ((CONTA_TESTE, 1), (CONTA_TESTE, 2))


def test_nao_resolve_candidatos_de_contas_diferentes():
    resolucao = resolver_item([(100, 1), (101, 2)])

    assert resolucao.status == 'ambiguo'
    assert resolucao.conta is None


def test_reclassifica_linha_sem_processo_quando_item_existe_no_oracle():
    localizada = demonstrativo(id_registro='localizada')
    ausente = demonstrativo(
        id_registro='ausente',
        valor_protocolo=Decimal('200.00'),
    )
    ambigua = demonstrativo(
        id_registro='ambigua',
        valor_protocolo=Decimal('300.00'),
    )
    itens_por_chave = {
        chave_item_demonstrativo(localizada, 10): [
            {'cd_reg': 100, 'cd_lancamento': 1},
            {'cd_reg': 100, 'cd_lancamento': 2},
        ],
        chave_item_demonstrativo(ambigua, 30): [
            {'cd_reg': 300, 'cd_lancamento': 1},
        ],
        chave_item_demonstrativo(ambigua, 31): [
            {'cd_reg': 301, 'cd_lancamento': 1},
        ],
    }

    classificacao = classificar_demonstrativos_sem_processo_por_oracle(
        [localizada, ausente, ambigua],
        {
            Decimal('100.00'): {10},
            Decimal('200.00'): {20},
            Decimal('300.00'): {30, 31},
        },
        itens_por_chave,
    )

    assert classificacao.identificadas == {'localizada': 10}
    assert [
        item['id_registro'] for item in classificacao.sem_correspondencia
    ] == ['ausente']
    assert [
        (item['id_registro'], remessas)
        for item, remessas in classificacao.ambiguas
    ] == [('ambigua', (30, 31))]


def test_aplica_nfse_recebimento_e_glosa_nas_tabelas_existentes(
    session,
    usuario_teste,
):
    chave_processo = ChaveProcesso(
        'P001/2026',
        date(2025, 12, 1),
        Decimal('100.00'),
    )
    item = ItemGlosaPlano(
        conta=500,
        cd_lancamento=1,
        demonstrativos=(
            {
                'id_registro': 'demo-1',
                'codigo_glosa': '1010',
                'descricao_servico': 'Procedimento de teste',
                'valor_glosa': Decimal('10.00'),
                'data_envio_lote': date(2026, 1, 10),
                'referencia': date(2025, 12, 1),
            },
        ),
        itens_oracle=(
            {
                'cd_paciente': 10,
                'nm_paciente': 'Paciente Teste',
                'cd_atendimento': 20,
                'cd_prestador': 30,
                'cd_convenio': 40,
                'tp_atendimento': 'Externo',
                'cd_pro_fat': 'SERVICO-1',
                'nm_convenio': 'IPM',
                'nr_guia': 'GUIA-1',
                'nm_prestador': 'Prestador Teste',
                'dt_atendimento': date(2025, 12, 15),
                'dt_lancamento': date(2025, 12, 15),
                'dt_alta': date(2025, 12, 15),
                'vl_total_conta': Decimal('100.00'),
                'qt_lancamento': Decimal('1.00'),
                'descricao': 'Procedimento de teste',
                'cd_gru_pro': 1,
                'ds_gru_pro': 'Grupo de procedimento',
                'cd_gru_fat': 2,
                'ds_gru_fat': 'Grupo de faturamento',
            },
        ),
    )
    remessa = RemessaPlano(
        processo=chave_processo,
        cd_remessa=1000,
        dados_oracle={
            'valor_total': Decimal('100.00'),
            'cnpj_convenio': '12345678000199',
            'convenio': 'IPM',
        },
        itens_glosa=(item,),
    )
    plano = ProcessoPlano(
        numero_processo='P001/2026',
        nota={'id_registro': 'nota-1', 'numero_nfse': '12345'},
        dados_processo={
            'status_processo': 'FINALIZADO',
            'data_abertura': date(2026, 1, 20),
        },
        conta_bancaria_id=CONTA_BANCARIA_TESTE,
        remessas=(remessa,),
    )

    totais = _aplicar_plano(session, (plano,), usuario_teste.id)

    conciliacao = session.query(ConciliacaoFaturamento).one()
    vinculo = session.query(ConciliacaoFaturamentoRemessa).one()
    recebimento = session.query(RecebimentoRemessa).one()
    registro = session.query(RegistroGlosa).one()
    rastreio = session.query(RegistroGlosaDemonstrativoIpm).one()
    financeira = session.query(RemessaFinanceira).one()
    assert totais == {
        'registros_glosa': 1,
        'linhas_demonstrativo': 1,
        'recebimentos': 1,
        'remessas': 1,
        'conciliacoes': 1,
    }
    assert conciliacao.valor_nfse == Decimal('90.00')
    assert conciliacao.data_recebimento == date(2026, 1, 20)
    assert conciliacao.conta_bancaria_id == CONTA_BANCARIA_TESTE
    assert vinculo.valor_glosado == Decimal('10.00')
    assert recebimento.valor_recebido == Decimal('90.00')
    assert recebimento.conta_bancaria_id == CONTA_BANCARIA_TESTE
    assert financeira.recebimento_integral is False
    assert registro.processo_controle_fatura_gab == 'P001/2026'
    assert registro.dt_pagamento == date(2026, 1, 20)
    assert rastreio.id_registro == 'demo-1'
    assert rastreio.registro_glosa_id == registro.id
