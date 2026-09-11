from decimal import Decimal

import pytest

from app_prontocardio.services import pdf_recurso_glosa
from app_prontocardio.services.pdf_recurso_glosa import (
    gerar_pdf_recurso_glosa,
    montar_linhas_recurso_glosa,
)

QUANTIDADE_LINHAS_ESPERADA = 2
QUANTIDADE_LINHAS_DUAS_REMESSAS = 4
TAMANHO_MINIMO_PDF = 1000


def _card_recurso():
    registro = {
        'id': 23261,
        'sn_ativo': 'true',
        'valor_recursado': '520.14',
        'dt_recurso': '2026-08-20',
        'numero_lote': 'LOTE-MAIDA-42',
        'descricao_glosa': 'Solicito análise da glosa.',
    }
    itens = [
        {
            'nm_paciente': 'Paciente Um',
            'numero_protocolo': '5519206',
            'dt_alta': '2026-04-18T10:00:00',
            'descricao': 'Procedimento individual',
            'qt_lancamento': '1',
            'valor_processado': '520.14',
            'valor_liberado': '400.11',
            'valor_glosa': '120.03',
            'motivo_glosa_descricao': 'Valor acima da tabela',
            'registro_recusa': registro,
        },
        {
            'nm_paciente': 'Paciente Um',
            'numero_protocolo': '5519206',
            'dt_alta': '2026-04-18T10:00:00',
            'descricao': 'Procedimento individual',
            'qt_lancamento': '1',
            'valor_processado': '1733.82',
            'valor_liberado': '1333.71',
            'valor_glosa': '400.11',
            'motivo_glosa_descricao': 'Valor acima da tabela',
            'registro_recusa': registro,
        },
    ]
    return {
        'cd_remessa': 17971,
        'numero_protocolo': '5519206',
        'processo': {'numero_processo': 'P193251/2026'},
        'pacientes': [{'itens': itens}],
    }


def test_pdf_rateia_recurso_sem_duplicar_itens_desmembrados():
    linhas = montar_linhas_recurso_glosa(_card_recurso())

    assert len(linhas) == QUANTIDADE_LINHAS_ESPERADA
    assert [linha['valor_recurso'] for linha in linhas] == [
        Decimal('120.03'),
        Decimal('400.11'),
    ]
    assert sum(
        linha['valor_recurso'] for linha in linhas
    ) == Decimal('520.14')
    assert {linha['lote'] for linha in linhas} == {'LOTE-MAIDA-42'}


def test_pdf_prioriza_descricao_individual_sobre_descricao_agrupada():
    card = _card_recurso()
    registro = card['pacientes'][0]['itens'][0]['registro_recusa']
    registro['descricao_glosa_agrupada'] = 'Descrição coletiva do recurso.'

    linhas = montar_linhas_recurso_glosa(card)

    assert {linha['justificativa'] for linha in linhas} == {
        'Solicito análise da glosa.'
    }


def test_pdf_usa_descricao_agrupada_quando_individual_esta_vazia():
    card = _card_recurso()
    registro = card['pacientes'][0]['itens'][0]['registro_recusa']
    registro['descricao_glosa'] = '   '
    registro['descricao_glosa_agrupada'] = 'Descrição coletiva do recurso.'

    linhas = montar_linhas_recurso_glosa(card)

    assert {linha['justificativa'] for linha in linhas} == {
        'Descrição coletiva do recurso.'
    }


def test_pdf_usa_descricao_coletiva_especifica_do_recurso():
    card = _card_recurso()
    registro = card['pacientes'][0]['itens'][0]['registro_recusa']
    registro['descricao_glosa'] = '   '
    registro['descricao_recurso_agrupada'] = 'Descrição dos itens marcados.'
    registro['descricao_glosa_agrupada'] = 'Descrição coletiva antiga.'

    linhas = montar_linhas_recurso_glosa(card)

    assert {linha['justificativa'] for linha in linhas} == {
        'Descrição dos itens marcados.'
    }


def test_gera_pdf_com_layout_de_recurso():
    conteudo = gerar_pdf_recurso_glosa(_card_recurso())

    assert conteudo.startswith(b'%PDF-')
    assert len(conteudo) > TAMANHO_MINIMO_PDF


def test_pdf_exibe_total_ao_final_da_coluna_valor_recurso(monkeypatch):
    tabelas = []
    tabela_original = pdf_recurso_glosa.Table

    def registrar_tabela(dados, *args, **kwargs):
        tabelas.append(dados)
        return tabela_original(dados, *args, **kwargs)

    monkeypatch.setattr(pdf_recurso_glosa, 'Table', registrar_tabela)

    gerar_pdf_recurso_glosa(_card_recurso())

    linha_total = tabelas[-1][-1]
    assert linha_total[10].getPlainText() == 'TOTAL'
    assert linha_total[11].getPlainText() == 'R$ 520,14'


def test_pdf_ipm_exibe_layout_com_remessa_sem_lote(monkeypatch):
    tabelas = []
    tabela_original = pdf_recurso_glosa.Table

    def registrar_tabela(dados, *args, **kwargs):
        tabelas.append(dados)
        return tabela_original(dados, *args, **kwargs)

    monkeypatch.setattr(pdf_recurso_glosa, 'Table', registrar_tabela)
    card = _card_recurso()
    gerar_pdf_recurso_glosa(card)

    tabela_itens = tabelas[-1]
    assert [celula.getPlainText() for celula in tabela_itens[0]][:4] == [
        'PROCESSOINICIAL', 'REMESSA', 'PACIENTE', 'ATEND.ALTA'
    ]
    assert tabela_itens[1][1].getPlainText() == '5519206'
    assert all(
        celula.getPlainText() != 'LOTE'
        for celula in tabela_itens[0]
    )


def test_pdf_issec_exibe_data_lote_maida_e_data_pagamento(monkeypatch):
    tabelas = []
    tabela_original = pdf_recurso_glosa.Table

    def registrar_tabela(dados, *args, **kwargs):
        tabelas.append(dados)
        return tabela_original(dados, *args, **kwargs)

    monkeypatch.setattr(pdf_recurso_glosa, 'Table', registrar_tabela)
    card = _card_recurso()
    card['convenio'] = 'ISSEC'
    registro = card['pacientes'][0]['itens'][0]['registro_recusa']
    registro['dt_pagamento'] = '2026-08-25'
    gerar_pdf_recurso_glosa(card)

    assert 'ISSEC' in tabelas[0][0][0].getPlainText()
    assert tabelas[1][0][2].getPlainText() == 'DATA DO PAGAMENTO'
    assert tabelas[1][1][2].getPlainText() == '25/08/2026'
    tabela_itens = tabelas[-1]
    assert [celula.getPlainText() for celula in tabela_itens[0]][:4] == [
        'PROCESSO', 'PACIENTE', 'DATA', 'LOTE MAIDA'
    ]
    assert tabela_itens[1][3].getPlainText() == 'LOTE-MAIDA-42'


def test_lote_continua_opcional_na_montagem_das_linhas():
    card = _card_recurso()

    card['pacientes'][0]['itens'][0]['registro_recusa']['numero_lote'] = None
    linhas = montar_linhas_recurso_glosa(card)
    assert linhas[0]['lote'] == '-'


def test_gera_pdf_consolidando_remessas_do_processo():
    primeira_remessa = _card_recurso()
    segunda_remessa = _card_recurso()
    segunda_remessa['cd_remessa'] = 17972
    segunda_remessa['numero_protocolo'] = '5519207'
    for item in segunda_remessa['pacientes'][0]['itens']:
        item['numero_protocolo'] = '5519207'
        item['registro_recusa'] = {
            **item['registro_recusa'],
            'id': 23262,
        }

    linhas = [
        linha
        for card in (primeira_remessa, segunda_remessa)
        for linha in montar_linhas_recurso_glosa(card)
    ]
    conteudo = gerar_pdf_recurso_glosa(
        [primeira_remessa, segunda_remessa]
    )

    assert len(linhas) == QUANTIDADE_LINHAS_DUAS_REMESSAS
    assert sum(
        linha['valor_recurso'] for linha in linhas
    ) == Decimal('1040.28')
    assert conteudo.startswith(b'%PDF-')


def test_pdf_exige_ao_menos_um_recurso_registrado():
    card = _card_recurso()
    for item in card['pacientes'][0]['itens']:
        item['registro_recusa'] = None

    with pytest.raises(
        ValueError,
        match='processo não possui recursos registrados',
    ):
        gerar_pdf_recurso_glosa(card)
