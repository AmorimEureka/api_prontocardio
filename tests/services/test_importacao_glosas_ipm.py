from datetime import date
from decimal import Decimal

from app_prontocardio.models import (
    AuditoriaConciliacaoFaturamento,
    ConciliacaoFaturamento,
    ConciliacaoFaturamentoRemessa,
    ProcessoConciliacaoRemessa,
    RecebimentoRemessa,
    RegistroGlosa,
    RegistroGlosaDemonstrativoIpm,
    RemessaFinanceira,
)
from app_prontocardio.services.importacao_glosas_ipm import (
    ChaveProcesso,
    associar_demonstrativos_a_processos,
    associar_processos_a_remessas,
    chave_item_atendimento_guia_coalesce_valor_demonstrativo,
    chave_item_atendimento_guia_coalesce_valor_oracle,
    chave_item_competencia_coalesce_valor_demonstrativo,
    chave_item_competencia_coalesce_valor_oracle,
    chave_item_demonstrativo,
    chave_item_lancamento_coalesce_demonstrativo,
    chave_item_lancamento_coalesce_oracle,
    chave_item_lancamento_dia_coalesce_demonstrativo,
    chave_item_lancamento_dia_coalesce_oracle,
    chave_item_lancamento_pro_fat_valor_demonstrativo,
    chave_item_lancamento_pro_fat_valor_oracle,
    chave_item_oracle,
    chave_item_sem_guia_demonstrativo,
    chave_item_sem_guia_oracle,
    chave_item_sem_guia_tuss_demonstrativo,
    chave_item_sem_guia_tuss_oracle,
    classificar_demonstrativos_sem_processo_por_oracle,
    indexar_itens_oracle,
    indexar_processos,
    normalizar_carteira,
    normalizar_competencia,
    normalizar_competencia_mensal,
    normalizar_mes_ano,
    resolver_correspondencia_item_oracle,
    resolver_item,
)
from scripts.importar_glosas_demonstrativo_ipm import (
    ItemGlosaPlano,
    ProcessoPlano,
    RemessaPlano,
    _aplicar_plano,
    _auditar_carga_substituivel,
    _preparar_itens_glosa,
    _remover_carga_planilha_anterior,
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
        'valor_processado': Decimal('100.00'),
        'data_realizacao': date(2025, 12, 15),
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


def test_normaliza_competencia_mensal_sem_persistir_primeiro_dia():
    assert normalizar_competencia_mensal(date(2025, 12, 31)) == '12/2025'
    assert normalizar_competencia_mensal('12/2025') == '12/2025'
    assert normalizar_competencia_mensal('01/26') == '01/2026'
    assert normalizar_competencia_mensal('inválida') is None


def test_associa_demonstrativo_por_protocolo_e_competencia_mensal():
    indice, _ = indexar_processos([
        processo(),
        processo(
            numero_processo='P002/2026',
            competencia_producao='01/2026',
            valor_protocolo=Decimal('200.00'),
        ),
    ])

    associacao = associar_demonstrativos_a_processos(
        [
            demonstrativo(),
            demonstrativo(
                id_registro='demo-sem-processo',
                data_realizacao=date(2026, 2, 15),
            ),
        ],
        indice,
    )

    assert associacao.unicas['demo-1'].numero_processo == 'P001/2026'
    assert [item['id_registro'] for item in associacao.sem_processo] == [
        'demo-sem-processo'
    ]
    assert associacao.ambiguas == ()


def test_valor_do_protocolo_desempata_mes_com_dois_processos():
    indice, _ = indexar_processos([
        processo(),
        processo(
            numero_processo='P002/2026',
            valor_protocolo=Decimal('200.00'),
        ),
    ])

    associacao = associar_demonstrativos_a_processos(
        [demonstrativo(valor_protocolo=Decimal('200.00'))],
        indice,
    )

    assert associacao.unicas['demo-1'].numero_processo == 'P002/2026'
    assert associacao.ambiguas == ()


def test_associa_protocolo_por_nr_origem_e_mes_da_data_realizacao():
    indice, _ = indexar_processos([
        processo(nr=None, nr_origem='PROTOCOLO-ORIGEM'),
    ])

    associacao = associar_demonstrativos_a_processos(
        [
            demonstrativo(
                numero_protocolo='protocolo-origem',
                data_realizacao=date(2025, 12, 31),
            )
        ],
        indice,
    )

    assert associacao.unicas['demo-1'].numero_processo == 'P001/2026'


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
        {(Decimal('100.00'), '12/2025'): {10, 11}},
    )

    assert associacao.unicas == {}
    assert associacao.ambiguas == ((primeiro, (10, 11)),)
    assert associacao.nao_encontradas == (segundo,)


def test_associa_processo_a_remessa_por_valor_e_competencia():
    processo = ChaveProcesso(
        'P001/2026',
        date(2022, 1, 1),
        Decimal('18450.96'),
    )

    associacao = associar_processos_a_remessas(
        [processo],
        {(Decimal('18450.96'), '01/2022'): {1000}},
    )

    assert associacao.unicas == {processo: 1000}


def test_chave_do_item_ignora_zeros_a_esquerda_da_carteira():
    demo = demonstrativo(codigo_beneficiario='123.456.789-0')
    oracle = {
        'cd_remessa': 10,
        'dt_competencia': date(2025, 12, 15),
        'nr_guia': 'guia-1',
        'cd_pro_fat': 'servico-1',
        'nr_carteira': '0000.123.456.789-0',
    }

    assert chave_item_demonstrativo(demo) == chave_item_oracle(oracle)
    assert normalizar_carteira('0002025080010920') == '2025080010920'


def test_primeira_chave_exige_mes_ano_guia_servico_e_carteira():
    demo = demonstrativo()
    oracle = {
        'cd_remessa': 10,
        'dt_competencia': date(2025, 12, 15),
        'nr_guia': 'GUIA-1',
        'cd_pro_fat': 'SERVICO-1',
        'nr_carteira': '001234567890',
    }
    chave = chave_item_demonstrativo(demo)

    assert chave == chave_item_oracle(oracle)
    assert chave == chave_item_oracle({
        **oracle,
        'dt_competencia': date(2025, 12, 1),
    })
    assert chave == chave_item_oracle({**oracle, 'cd_remessa': 999})
    assert chave != chave_item_oracle({
        **oracle,
        'dt_competencia': date(2025, 11, 15),
    })
    assert chave != chave_item_oracle({**oracle, 'nr_guia': 'OUTRA-GUIA'})
    assert chave != chave_item_oracle({
        **oracle,
        'cd_pro_fat': 'OUTRO-SERVICO',
    })
    assert chave != chave_item_oracle({
        **oracle,
        'nr_carteira': '009999999999',
    })


def test_segunda_chave_usa_mes_ano_e_desconsidera_guia():
    demo = demonstrativo(numero_guia_senha='GUIA-DEMONSTRATIVO')
    oracle = {
        'cd_remessa': 10,
        'dt_competencia': date(2025, 12, 15),
        'nr_guia': 'GUIA-ORACLE',
        'cd_pro_fat': 'SERVICO-1',
        'nr_carteira': '001234567890',
    }

    assert normalizar_mes_ano('15/12/2025') == (2025, 12)
    assert chave_item_sem_guia_demonstrativo(
        demo
    ) == chave_item_sem_guia_oracle(oracle)
    assert chave_item_sem_guia_demonstrativo(
        demo,
    ) == chave_item_sem_guia_oracle({
        **oracle,
        'dt_competencia': date(2025, 12, 1),
    })
    assert chave_item_sem_guia_demonstrativo(
        demo,
    ) != chave_item_sem_guia_oracle({
        **oracle,
        'dt_competencia': date(2025, 11, 15),
    })
    assert chave_item_sem_guia_demonstrativo(
        demo,
    ) != chave_item_sem_guia_oracle({**oracle, 'cd_pro_fat': 'OUTRO-SERVICO'})
    assert chave_item_sem_guia_demonstrativo(
        demo,
    ) != chave_item_sem_guia_oracle({**oracle, 'nr_carteira': '009999999999'})


def test_terceira_chave_usa_mes_ano_tuss_carteira_e_desconsidera_guia():
    demo = demonstrativo(
        numero_guia_senha='GUIA-DEMONSTRATIVO',
        codigo_servico='TUSS-1',
        valor_protocolo=Decimal('999.99'),
        valor_processado=Decimal('100.00'),
    )
    oracle = {
        'cd_remessa': 10,
        'dt_competencia': date(2025, 12, 15),
        'nr_guia': 'GUIA-ORACLE',
        'cd_pro_fat': 'SERVICO-INTERNO',
        'cd_tuss': 'TUSS-1',
        'nr_carteira': '001234567890',
        'vl_total_conta': Decimal('100.00'),
    }

    assert chave_item_sem_guia_tuss_demonstrativo(
        demo
    ) == chave_item_sem_guia_tuss_oracle(oracle)
    assert chave_item_sem_guia_tuss_demonstrativo(
        demo,
    ) != chave_item_sem_guia_tuss_oracle({
        **oracle,
        'dt_competencia': date(2025, 11, 15),
    })
    assert chave_item_sem_guia_tuss_demonstrativo(
        demo,
    ) != chave_item_sem_guia_tuss_oracle({**oracle, 'cd_tuss': 'OUTRO-TUSS'})
    assert chave_item_sem_guia_tuss_demonstrativo(
        demo,
    ) != chave_item_sem_guia_tuss_oracle({
        **oracle,
        'nr_carteira': '009999999999',
    })
    assert chave_item_sem_guia_tuss_demonstrativo(
        demo,
    ) != chave_item_sem_guia_tuss_oracle({
        **oracle,
        'vl_total_conta': Decimal('100.01'),
    })


def test_quarta_chave_usa_lancamento_coalesce_servico_e_carteira():
    demo = demonstrativo(
        codigo_servico='SERVICO-INTERNO',
        data_realizacao=date(2025, 12, 1),
    )
    oracle = {
        'dt_lancamento': date(2025, 12, 31),
        'cd_pro_fat': 'SERVICO-INTERNO',
        'cd_tuss': 'TUSS-ALTERNATIVO',
        'nr_carteira': '001234567890',
    }

    assert chave_item_lancamento_coalesce_demonstrativo(
        demo,
    ) == chave_item_lancamento_coalesce_oracle(oracle)
    assert chave_item_lancamento_coalesce_demonstrativo(
        {**demo, 'codigo_servico': 'TUSS-ALTERNATIVO'},
    ) == chave_item_lancamento_coalesce_oracle({
        **oracle,
        'cd_pro_fat': None,
    })
    assert chave_item_lancamento_coalesce_demonstrativo(
        demo,
    ) != chave_item_lancamento_coalesce_oracle({
        **oracle,
        'dt_lancamento': date(2025, 11, 30),
    })
    assert chave_item_lancamento_coalesce_demonstrativo(
        demo,
    ) != chave_item_lancamento_coalesce_oracle({
        **oracle,
        'nr_carteira': '009999999999',
    })


def test_data_exata_refina_itens_ambiguos_no_mes():
    linha = demonstrativo(
        id_registro='resolvida-pelo-dia',
        numero_guia_senha='GUIA-DEMONSTRATIVO',
        codigo_servico='TUSS-4',
        data_realizacao=date(2025, 12, 10),
    )
    item_base = {
        'cd_remessa': 10,
        'nr_guia': 'OUTRA-GUIA',
        'cd_pro_fat': None,
        'cd_tuss': 'TUSS-4',
        'nr_carteira': '001234567890',
        'vl_total_conta': Decimal('100.00'),
        'dt_competencia': date(2025, 12, 1),
        'cd_lancamento': 1,
    }
    itens_oracle = [
        {
            **item_base,
            'cd_reg': 100,
            'dt_lancamento': date(2025, 12, 10),
        },
        {
            **item_base,
            'cd_reg': 101,
            'dt_lancamento': date(2025, 12, 12),
        },
    ]

    assert chave_item_lancamento_dia_coalesce_demonstrativo(
        linha,
    ) == chave_item_lancamento_dia_coalesce_oracle(itens_oracle[0])
    classificacao = classificar_demonstrativos_sem_processo_por_oracle(
        [linha],
        indexar_itens_oracle(itens_oracle),
    )

    assert classificacao.identificadas == {'resolvida-pelo-dia': 10}
    assert classificacao.criterios == {
        'resolvida-pelo-dia': (
            'lancamento_dia_coalesce_servico_carteira'
        ),
    }
    assert classificacao.ambiguas == ()


def test_data_exata_refina_lancamentos_da_mesma_conta_e_guia():
    linha = demonstrativo(
        id_registro='lancamento-exato-mesma-conta',
        numero_guia_senha='GUIA-502043',
        codigo_servico='00010081',
        data_realizacao=date(2026, 3, 5),
    )
    item_base = {
        'cd_remessa': 17379,
        'cd_reg': 22264,
        'nr_guia': 'GUIA-502043',
        'cd_pro_fat': '00010081',
        'cd_tuss': '00010081',
        'nr_carteira': '001166865008',
        'vl_total_conta': Decimal('115.00'),
        'dt_competencia': date(2026, 3, 1),
    }
    itens_oracle = [
        {
            **item_base,
            'cd_lancamento': 116,
            'dt_lancamento': date(2026, 3, 3),
        },
        {
            **item_base,
            'cd_lancamento': 117,
            'dt_lancamento': date(2026, 3, 5),
        },
    ]

    correspondencia = resolver_correspondencia_item_oracle(
        linha,
        indexar_itens_oracle(itens_oracle),
        cd_remessa_esperada=17379,
    )

    assert correspondencia.status == 'item_unico'
    assert correspondencia.criterio == (
        'lancamento_dia_coalesce_servico_carteira'
    )
    assert correspondencia.resolucao is not None
    assert correspondencia.resolucao.cd_lancamento == 117


def test_quinta_chave_usa_competencia_coalesce_servico_e_valor():
    demo = demonstrativo(
        codigo_servico='TUSS-ALTERNATIVO',
        codigo_beneficiario='1111111111',
        data_realizacao=date(2025, 12, 1),
        valor_processado=Decimal('42.15'),
    )
    oracle = {
        'dt_competencia': date(2025, 12, 31),
        'cd_pro_fat': 'SERVICO-INTERNO',
        'cd_tuss': 'TUSS-ALTERNATIVO',
        'nr_carteira': '2222222222',
        'vl_total_conta': Decimal('42.15'),
    }

    assert chave_item_competencia_coalesce_valor_demonstrativo(
        demo,
    ) == chave_item_competencia_coalesce_valor_oracle(oracle)
    assert chave_item_competencia_coalesce_valor_demonstrativo(
        {**demo, 'codigo_servico': 'SERVICO-INTERNO'},
    ) == chave_item_competencia_coalesce_valor_oracle({
        **oracle,
        'cd_tuss': None,
    })
    assert chave_item_competencia_coalesce_valor_demonstrativo(
        demo,
    ) != chave_item_competencia_coalesce_valor_oracle({
        **oracle,
        'dt_competencia': date(2025, 11, 30),
    })
    assert chave_item_competencia_coalesce_valor_demonstrativo(
        demo,
    ) != chave_item_competencia_coalesce_valor_oracle({
        **oracle,
        'vl_total_conta': Decimal('42.16'),
    })


def test_sexta_chave_usa_atendimento_guia_coalesce_servico_e_valor():
    demo = demonstrativo(
        codigo_servico='TUSS-ALTERNATIVO',
        codigo_beneficiario='1111111111',
        data_realizacao=date(2025, 12, 1),
        numero_guia_senha='GUIA-SEXTA',
        valor_processado=Decimal('42.15'),
    )
    oracle = {
        'dt_atendimento': date(2025, 12, 31),
        'nr_guia': 'GUIA-SEXTA',
        'cd_pro_fat': 'SERVICO-INTERNO',
        'cd_tuss': 'TUSS-ALTERNATIVO',
        'nr_carteira': '2222222222',
        'vl_total_conta': Decimal('42.15'),
    }

    assert chave_item_atendimento_guia_coalesce_valor_demonstrativo(
        demo,
    ) == chave_item_atendimento_guia_coalesce_valor_oracle(oracle)
    assert chave_item_atendimento_guia_coalesce_valor_demonstrativo(
        {**demo, 'codigo_servico': 'SERVICO-INTERNO'},
    ) == chave_item_atendimento_guia_coalesce_valor_oracle({
        **oracle,
        'cd_tuss': None,
    })
    assert chave_item_atendimento_guia_coalesce_valor_demonstrativo(
        demo,
    ) != chave_item_atendimento_guia_coalesce_valor_oracle({
        **oracle,
        'dt_atendimento': date(2025, 11, 30),
    })
    assert chave_item_atendimento_guia_coalesce_valor_demonstrativo(
        demo,
    ) != chave_item_atendimento_guia_coalesce_valor_oracle({
        **oracle,
        'nr_guia': 'OUTRA-GUIA',
    })
    assert chave_item_atendimento_guia_coalesce_valor_demonstrativo(
        demo,
    ) != chave_item_atendimento_guia_coalesce_valor_oracle({
        **oracle,
        'vl_total_conta': Decimal('42.16'),
    })


def test_setima_chave_usa_lancamento_pro_fat_carteira_e_valor():
    demo = demonstrativo(
        codigo_servico='SERVICO-INTERNO',
        codigo_beneficiario='1234567890',
        data_realizacao=date(2025, 12, 1),
        valor_processado=Decimal('42.15'),
    )
    oracle = {
        'dt_lancamento': date(2025, 12, 31),
        'cd_pro_fat': 'SERVICO-INTERNO',
        'cd_tuss': 'TUSS-ALTERNATIVO',
        'nr_carteira': '001234567890',
        'vl_total_conta': Decimal('42.15'),
    }

    assert chave_item_lancamento_pro_fat_valor_demonstrativo(
        demo,
    ) == chave_item_lancamento_pro_fat_valor_oracle(oracle)
    assert chave_item_lancamento_pro_fat_valor_demonstrativo(
        demo,
    ) != chave_item_lancamento_pro_fat_valor_oracle({
        **oracle,
        'dt_lancamento': date(2025, 11, 30),
    })
    assert chave_item_lancamento_pro_fat_valor_demonstrativo(
        demo,
    ) != chave_item_lancamento_pro_fat_valor_oracle({
        **oracle,
        'cd_pro_fat': None,
    })
    assert chave_item_lancamento_pro_fat_valor_demonstrativo(
        demo,
    ) != chave_item_lancamento_pro_fat_valor_oracle({
        **oracle,
        'nr_carteira': '009999999999',
    })
    assert chave_item_lancamento_pro_fat_valor_demonstrativo(
        demo,
    ) != chave_item_lancamento_pro_fat_valor_oracle({
        **oracle,
        'vl_total_conta': Decimal('42.16'),
    })


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
        codigo_servico='SERVICO-AUSENTE',
    )
    ambigua = demonstrativo(
        id_registro='ambigua',
        valor_protocolo=Decimal('300.00'),
        numero_guia_senha='GUIA-AMBIGUA',
    )
    localizada_por_conta = demonstrativo(
        id_registro='localizada-por-conta',
        valor_protocolo=Decimal('400.00'),
        codigo_servico='SERVICO-DEMONSTRATIVO',
        codigo_beneficiario='1234567890',
    )
    item_oracle_mesma_conta = {
        'cd_remessa': 40,
        'dt_competencia': date(2025, 12, 15),
        'nr_guia': 'GUIA-1',
        'cd_pro_fat': 'SERVICO-ORACLE',
        'nr_carteira': '001234567890',
    }
    localizada_por_competencia = demonstrativo(
        id_registro='localizada-por-competencia',
        valor_protocolo=Decimal('500.00'),
        numero_guia_senha='GUIA-DEMONSTRATIVO',
        codigo_servico='SERVICO-COMPETENCIA',
    )
    item_oracle_mesma_competencia = {
        'cd_remessa': 50,
        'nr_guia': 'GUIA-ORACLE',
        'cd_pro_fat': 'SERVICO-COMPETENCIA',
        'nr_carteira': '001234567890',
        'dt_competencia': date(2025, 12, 15),
    }
    itens_oracle = [
        {
            'cd_remessa': 10,
            'dt_competencia': date(2025, 12, 1),
            'nr_guia': 'GUIA-1',
            'cd_pro_fat': 'SERVICO-1',
            'nr_carteira': '001234567890',
            'cd_reg': 100,
            'cd_lancamento': lancamento,
        }
        for lancamento in (1, 2)
    ]
    itens_oracle.extend([
        {
            'cd_remessa': remessa,
            'dt_competencia': date(2025, 12, 1),
            'nr_guia': 'GUIA-AMBIGUA',
            'cd_pro_fat': 'SERVICO-1',
            'nr_carteira': '001234567890',
            'cd_reg': remessa * 10,
            'cd_lancamento': 1,
        }
        for remessa in (30, 31)
    ])
    itens_oracle.extend([
        {
            **item_oracle_mesma_conta,
            'cd_reg': 400,
            'cd_lancamento': 1,
        },
        {
            **item_oracle_mesma_competencia,
            'cd_reg': 500,
            'cd_lancamento': 1,
        },
    ])

    classificacao = classificar_demonstrativos_sem_processo_por_oracle(
        [
            localizada,
            ausente,
            ambigua,
            localizada_por_conta,
            localizada_por_competencia,
        ],
        indexar_itens_oracle(itens_oracle),
    )

    assert classificacao.identificadas == {
        'localizada': 10,
        'localizada-por-competencia': 50,
    }
    assert classificacao.criterios == {
        'localizada': 'competencia_guia_servico_carteira',
        'localizada-por-competencia': 'competencia_servico_carteira',
    }
    assert [
        item['id_registro'] for item in classificacao.sem_correspondencia
    ] == ['ausente', 'localizada-por-conta']
    assert [
        (item['id_registro'], remessas)
        for item, remessas in classificacao.ambiguas
    ] == [('ambigua', (30, 31))]


def test_nao_reclassifica_pela_segunda_chave_quando_conta_e_ambigua():
    linha = demonstrativo(
        id_registro='conta-ambigua',
    )
    item_oracle = {
        'cd_remessa': 10,
        'dt_competencia': date(2025, 12, 15),
        'nr_guia': 'GUIA-ORACLE',
        'cd_pro_fat': 'SERVICO-1',
        'nr_carteira': '001234567890',
    }

    classificacao = classificar_demonstrativos_sem_processo_por_oracle(
        [linha],
        indexar_itens_oracle([
            {**item_oracle, 'cd_reg': 100, 'cd_lancamento': 1},
            {**item_oracle, 'cd_reg': 101, 'cd_lancamento': 2},
        ]),
    )

    assert classificacao.identificadas == {}
    assert classificacao.sem_correspondencia == ()
    assert [
        (item['id_registro'], remessas)
        for item, remessas in classificacao.ambiguas
    ] == [('conta-ambigua', (10,))]


def test_chave_alternativa_nao_repete_correspondencia_da_chave_anterior():
    linha = demonstrativo(id_registro='prioridade-chave-anterior')
    item_chave_anterior = {
        'cd_remessa': 10,
        'nr_guia': 'GUIA-1',
        'cd_pro_fat': 'SERVICO-1',
        'nr_carteira': '001234567890',
        'dt_competencia': date(2025, 12, 15),
        'cd_reg': 100,
        'cd_lancamento': 1,
    }
    item_alternativo = {
        'cd_remessa': 11,
        'nr_guia': 'OUTRA-GUIA',
        'cd_pro_fat': 'SERVICO-1',
        'nr_carteira': '001234567890',
        'dt_competencia': date(2025, 12, 15),
    }

    classificacao = classificar_demonstrativos_sem_processo_por_oracle(
        [linha],
        indexar_itens_oracle([
            item_chave_anterior,
            {
                **item_alternativo,
                'cd_reg': 110,
                'cd_lancamento': 1,
            },
        ]),
    )

    assert classificacao.identificadas == {
        'prioridade-chave-anterior': 10,
    }
    assert classificacao.criterios == {
        'prioridade-chave-anterior': 'competencia_guia_servico_carteira',
    }
    assert classificacao.ambiguas == ()


def test_terceira_chave_so_classifica_linha_nao_resolvida_pelas_anteriores():
    linha = demonstrativo(
        id_registro='localizada-por-tuss',
        codigo_servico='TUSS-1',
    )
    item_oracle = {
        'cd_remessa': 10,
        'nr_guia': 'OUTRA-GUIA',
        'cd_pro_fat': 'SERVICO-INTERNO',
        'cd_tuss': 'TUSS-1',
        'nr_carteira': '001234567890',
        'vl_total_conta': Decimal('100.00'),
        'dt_competencia': date(2025, 12, 15),
        'cd_reg': 100,
        'cd_lancamento': 1,
    }

    classificacao = classificar_demonstrativos_sem_processo_por_oracle(
        [linha],
        indexar_itens_oracle([item_oracle]),
    )

    assert classificacao.identificadas == {'localizada-por-tuss': 10}
    assert classificacao.criterios == {
        'localizada-por-tuss': 'competencia_tuss_carteira',
    }
    assert classificacao.sem_correspondencia == ()
    assert classificacao.ambiguas == ()


def test_terceira_chave_refina_ambiguidade_da_segunda_pelo_valor_processado():
    linha = demonstrativo(
        id_registro='ambigua-na-segunda',
        numero_guia_senha='GUIA-DEMONSTRATIVO',
        codigo_servico='TUSS-1',
    )
    item_base = {
        'nr_guia': 'OUTRA-GUIA',
        'cd_pro_fat': 'TUSS-1',
        'cd_tuss': 'TUSS-1',
        'nr_carteira': '001234567890',
        'dt_competencia': date(2025, 12, 15),
        'cd_lancamento': 1,
    }
    itens_oracle = [
        {
            **item_base,
            'cd_remessa': 10,
            'cd_reg': 100,
            'vl_total_conta': Decimal('99.99'),
        },
        {
            **item_base,
            'cd_remessa': 11,
            'cd_reg': 110,
            'vl_total_conta': Decimal('100.00'),
        },
    ]

    classificacao = classificar_demonstrativos_sem_processo_por_oracle(
        [linha],
        indexar_itens_oracle(itens_oracle),
    )

    assert classificacao.identificadas == {'ambigua-na-segunda': 11}
    assert classificacao.criterios == {
        'ambigua-na-segunda': 'competencia_tuss_carteira',
    }
    assert classificacao.sem_correspondencia == ()
    assert classificacao.ambiguas == ()


def test_quarta_chave_classifica_linha_nao_resolvida_pelas_anteriores():
    linha = demonstrativo(
        id_registro='localizada-por-lancamento',
        numero_guia_senha='GUIA-DEMONSTRATIVO',
        codigo_servico='TUSS-4',
    )
    item_oracle = {
        'cd_remessa': 10,
        'nr_guia': 'OUTRA-GUIA',
        'cd_pro_fat': None,
        'cd_tuss': 'TUSS-4',
        'nr_carteira': '001234567890',
        'vl_total_conta': Decimal('99.99'),
        'dt_competencia': date(2025, 11, 30),
        'dt_lancamento': date(2025, 12, 31),
        'cd_reg': 100,
        'cd_lancamento': 1,
    }

    classificacao = classificar_demonstrativos_sem_processo_por_oracle(
        [linha],
        indexar_itens_oracle([item_oracle]),
    )

    assert classificacao.identificadas == {'localizada-por-lancamento': 10}
    assert classificacao.criterios == {
        'localizada-por-lancamento': (
            'lancamento_coalesce_servico_carteira'
        ),
    }
    assert classificacao.sem_correspondencia == ()
    assert classificacao.ambiguas == ()


def test_quinta_chave_classifica_linha_nao_resolvida_pelas_anteriores():
    linha = demonstrativo(
        id_registro='localizada-por-competencia-valor',
        numero_guia_senha='GUIA-DEMONSTRATIVO',
        codigo_servico='TUSS-5',
        codigo_beneficiario='1111111111',
        valor_processado=Decimal('42.15'),
    )
    item_oracle = {
        'cd_remessa': 10,
        'nr_guia': 'OUTRA-GUIA',
        'cd_pro_fat': 'SERVICO-INTERNO',
        'cd_tuss': 'TUSS-5',
        'nr_carteira': '2222222222',
        'vl_total_conta': Decimal('42.15'),
        'dt_competencia': date(2025, 12, 31),
        'dt_lancamento': date(2025, 11, 30),
        'cd_reg': 100,
        'cd_lancamento': 1,
    }

    classificacao = classificar_demonstrativos_sem_processo_por_oracle(
        [linha],
        indexar_itens_oracle([item_oracle]),
    )

    assert classificacao.identificadas == {
        'localizada-por-competencia-valor': 10,
    }
    assert classificacao.criterios == {
        'localizada-por-competencia-valor': (
            'competencia_coalesce_servico_valor'
        ),
    }
    assert classificacao.sem_correspondencia == ()
    assert classificacao.ambiguas == ()


def test_sexta_chave_refina_ambiguidade_anterior_com_atendimento_e_guia():
    linha = demonstrativo(
        id_registro='localizada-por-atendimento-guia-valor',
        numero_guia_senha='GUIA-SEXTA',
        codigo_servico='TUSS-6',
        codigo_beneficiario='000123',
        valor_processado=Decimal('42.15'),
    )
    itens_oracle = [
        {
            'cd_remessa': cd_remessa,
            'nr_guia': f'OUTRA-GUIA-{cd_remessa}',
            'cd_pro_fat': 'TUSS-6',
            'cd_tuss': 'OUTRO-TUSS',
            'nr_carteira': '000123',
            'vl_total_conta': Decimal('99.99'),
            'dt_competencia': date(2025, 12, 1),
            'dt_lancamento': date(2025, 11, 1),
            'dt_atendimento': date(2025, 11, 1),
            'cd_reg': cd_remessa * 10,
            'cd_lancamento': 1,
        }
        for cd_remessa in (20, 21)
    ]
    itens_oracle.append({
        'cd_remessa': 10,
        'nr_guia': 'GUIA-SEXTA',
        'cd_pro_fat': 'SERVICO-INTERNO',
        'cd_tuss': 'TUSS-6',
        'nr_carteira': '999999',
        'vl_total_conta': Decimal('42.15'),
        'dt_competencia': date(2025, 11, 1),
        'dt_lancamento': date(2025, 11, 1),
        'dt_atendimento': date(2025, 12, 31),
        'cd_reg': 100,
        'cd_lancamento': 1,
    })

    classificacao = classificar_demonstrativos_sem_processo_por_oracle(
        [linha],
        indexar_itens_oracle(itens_oracle),
    )

    assert classificacao.identificadas == {
        'localizada-por-atendimento-guia-valor': 10,
    }
    assert classificacao.criterios == {
        'localizada-por-atendimento-guia-valor': (
            'atendimento_guia_coalesce_servico_valor'
        ),
    }
    assert classificacao.sem_correspondencia == ()
    assert classificacao.ambiguas == ()


def test_setima_chave_refina_coalesce_com_pro_fat_carteira_e_valor():
    linha = demonstrativo(
        id_registro='localizada-por-lancamento-pro-fat',
        numero_guia_senha='GUIA-DEMONSTRATIVO',
        codigo_servico='SERVICO-7',
        codigo_beneficiario='000123',
    )
    item_pro_fat = {
        'cd_remessa': 10,
        'nr_guia': 'OUTRA-GUIA',
        'cd_pro_fat': 'SERVICO-7',
        'cd_tuss': 'OUTRO-TUSS',
        'nr_carteira': '000123',
        'vl_total_conta': Decimal('100.00'),
        'dt_competencia': date(2025, 11, 1),
        'dt_lancamento': date(2025, 12, 31),
        'dt_atendimento': date(2025, 11, 1),
        'cd_reg': 100,
        'cd_lancamento': 1,
    }
    item_tuss = {
        **item_pro_fat,
        'cd_remessa': 20,
        'cd_pro_fat': None,
        'cd_tuss': 'SERVICO-7',
        'cd_reg': 200,
    }
    item_pro_fat_valor_divergente = {
        **item_pro_fat,
        'cd_remessa': 30,
        'vl_total_conta': Decimal('99.99'),
        'cd_reg': 300,
    }

    classificacao = classificar_demonstrativos_sem_processo_por_oracle(
        [linha],
        indexar_itens_oracle([
            item_pro_fat,
            item_tuss,
            item_pro_fat_valor_divergente,
        ]),
    )

    assert classificacao.identificadas == {
        'localizada-por-lancamento-pro-fat': 10,
    }
    assert classificacao.criterios == {
        'localizada-por-lancamento-pro-fat': (
            'lancamento_pro_fat_carteira_valor'
        ),
    }
    assert classificacao.sem_correspondencia == ()
    assert classificacao.ambiguas == ()


def test_prepara_glosas_pelas_quatro_chaves_sem_incluir_remessa_na_chave():
    chave_processo = ChaveProcesso(
        'P001/2026',
        date(2025, 12, 1),
        Decimal('100.00'),
    )
    demonstrativos = [
        demonstrativo(
            id_registro='primeira',
            codigo_servico='SERVICO-1',
            codigo_beneficiario='000123',
            valor_glosa=Decimal('1.00'),
        ),
        demonstrativo(
            id_registro='segunda',
            numero_guia_senha='GUIA-DIFERENTE',
            codigo_servico='SERVICO-2',
            codigo_beneficiario='000456',
            valor_glosa=Decimal('2.00'),
        ),
        demonstrativo(
            id_registro='terceira',
            numero_guia_senha='GUIA-DIFERENTE',
            codigo_servico='TUSS-3',
            codigo_beneficiario='000789',
            valor_glosa=Decimal('3.00'),
        ),
        demonstrativo(
            id_registro='quarta',
            numero_guia_senha='GUIA-DIFERENTE',
            codigo_servico='TUSS-4',
            codigo_beneficiario='000987',
            valor_glosa=Decimal('4.00'),
        ),
    ]
    itens_oracle = [
        {
            'cd_remessa': 10,
            'cd_reg': 101,
            'cd_lancamento': 1,
            'dt_competencia': date(2025, 12, 1),
            'nr_guia': 'GUIA-1',
            'cd_pro_fat': 'SERVICO-1',
            'cd_tuss': 'TUSS-1',
            'nr_carteira': '123',
        },
        {
            'cd_remessa': 10,
            'cd_reg': 102,
            'cd_lancamento': 2,
            'dt_competencia': date(2025, 12, 31),
            'nr_guia': 'GUIA-ORACLE',
            'cd_pro_fat': 'SERVICO-2',
            'cd_tuss': 'TUSS-2',
            'nr_carteira': '000456',
        },
        {
            'cd_remessa': 10,
            'cd_reg': 103,
            'cd_lancamento': 3,
            'dt_competencia': date(2025, 12, 15),
            'nr_guia': 'GUIA-ORACLE',
            'cd_pro_fat': 'SERVICO-INTERNO',
            'cd_tuss': 'TUSS-3',
            'nr_carteira': '000789',
            'vl_total_conta': Decimal('100.00'),
        },
        {
            'cd_remessa': 10,
            'cd_reg': 104,
            'cd_lancamento': 4,
            'dt_competencia': date(2025, 11, 30),
            'dt_lancamento': date(2025, 12, 31),
            'nr_guia': 'GUIA-ORACLE',
            'cd_pro_fat': None,
            'cd_tuss': 'TUSS-4',
            'nr_carteira': '000987',
            'vl_total_conta': Decimal('99.99'),
        },
    ]
    itens_por_identidade = {
        (item['cd_remessa'], item['cd_reg'], item['cd_lancamento']): item
        for item in itens_oracle
    }

    itens, pendencias, avaliados = _preparar_itens_glosa(
        demonstrativos,
        {item['id_registro']: chave_processo for item in demonstrativos},
        {chave_processo: 10},
        indexar_itens_oracle(itens_oracle),
        itens_por_identidade,
    )

    assert [(item.conta, item.cd_lancamento) for item in itens[10]] == [
        (101, 1),
        (102, 2),
        (103, 3),
        (104, 4),
    ]
    assert pendencias == []
    assert avaliados == {'primeira', 'segunda', 'terceira', 'quarta'}


def test_bloqueia_glosa_quando_remessa_do_item_diverge_da_remessa_processo():
    chave_processo = ChaveProcesso(
        'P001/2026',
        date(2025, 12, 1),
        Decimal('100.00'),
    )
    linha = demonstrativo(valor_glosa=Decimal('1.00'))
    item_oracle = {
        'cd_remessa': 11,
        'cd_reg': 101,
        'cd_lancamento': 1,
        'dt_competencia': date(2025, 12, 1),
        'nr_guia': 'GUIA-1',
        'cd_pro_fat': 'SERVICO-1',
        'cd_tuss': 'TUSS-1',
        'nr_carteira': '1234567890',
    }

    itens, pendencias, avaliados = _preparar_itens_glosa(
        [linha],
        {'demo-1': chave_processo},
        {chave_processo: 10},
        indexar_itens_oracle([item_oracle]),
        {(11, 101, 1): item_oracle},
    )

    assert itens == {}
    assert pendencias[0]['motivo'] == 'remessa_divergente'
    assert avaliados == {'demo-1'}


def test_continua_apos_divergencia_e_usa_sexta_chave_na_remessa_esperada():
    chave_processo = ChaveProcesso(
        'P001/2026',
        date(2025, 12, 1),
        Decimal('100.00'),
    )
    linha = demonstrativo(
        codigo_servico='TUSS-6',
        valor_glosa=Decimal('1.00'),
    )
    item_divergente = {
        'cd_remessa': 11,
        'cd_reg': 110,
        'cd_lancamento': 1,
        'dt_competencia': date(2025, 12, 1),
        'dt_lancamento': date(2025, 11, 1),
        'dt_atendimento': date(2025, 11, 1),
        'nr_guia': 'GUIA-1',
        'cd_pro_fat': 'TUSS-6',
        'cd_tuss': 'OUTRO-TUSS',
        'nr_carteira': '1234567890',
        'vl_total_conta': Decimal('99.99'),
    }
    item_esperado = {
        'cd_remessa': 10,
        'cd_reg': 100,
        'cd_lancamento': 2,
        'dt_competencia': date(2025, 11, 1),
        'dt_lancamento': date(2025, 11, 1),
        'dt_atendimento': date(2025, 12, 31),
        'nr_guia': 'GUIA-1',
        'cd_pro_fat': 'SERVICO-INTERNO',
        'cd_tuss': 'TUSS-6',
        'nr_carteira': '999999',
        'vl_total_conta': Decimal('100.00'),
    }
    itens_oracle = [item_divergente, item_esperado]
    itens_por_identidade = {
        (item['cd_remessa'], item['cd_reg'], item['cd_lancamento']): item
        for item in itens_oracle
    }

    itens, pendencias, avaliados = _preparar_itens_glosa(
        [linha],
        {'demo-1': chave_processo},
        {chave_processo: 10},
        indexar_itens_oracle(itens_oracle),
        itens_por_identidade,
    )

    assert [(item.conta, item.cd_lancamento) for item in itens[10]] == [
        (100, 2),
    ]
    assert pendencias == []
    assert avaliados == {'demo-1'}


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

    carga_anterior = _auditar_carga_substituivel(session)
    assert carga_anterior == {
        'conciliacoes_faturamento': 1,
        'conciliacoes_faturamento_remessas': 1,
        'registros_glosa': 1,
        'registros_glosa_triagem': 0,
        'registros_glosa_sem_vinculo': 0,
        'recebimentos_remessas': 1,
        'processos_conciliacao_remessa': 1,
        'remessas_financeiras': 1,
    }

    removidos = _remover_carga_planilha_anterior(session)
    session.commit()

    assert removidos['registros_glosa_demonstrativo_ipm'] == 1
    assert removidos['registros_glosa'] == 1
    assert removidos['recebimentos_remessas'] == 1
    assert removidos['auditorias_conciliacao_faturamento'] == 1
    assert removidos['conciliacoes_faturamento_remessas'] == 1
    assert removidos['conciliacoes_faturamento'] == 1
    assert removidos['processos_conciliacao_remessa'] == 1
    assert removidos['remessas_financeiras'] == 1
    assert session.query(RegistroGlosa).count() == 0
    assert session.query(RegistroGlosaDemonstrativoIpm).count() == 0
    assert session.query(RecebimentoRemessa).count() == 0
    assert session.query(AuditoriaConciliacaoFaturamento).count() == 0
    assert session.query(ConciliacaoFaturamentoRemessa).count() == 0
    assert session.query(ConciliacaoFaturamento).count() == 0
    assert session.query(ProcessoConciliacaoRemessa).count() == 0
    assert session.query(RemessaFinanceira).count() == 0
