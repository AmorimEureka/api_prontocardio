from decimal import Decimal

import pytest

from app_prontocardio.services.pdf_recurso_glosa import (
    gerar_pdf_recurso_glosa,
    montar_linhas_recurso_glosa,
)

QUANTIDADE_LINHAS_ESPERADA = 2
TAMANHO_MINIMO_PDF = 1000


def _card_recurso():
    registro = {
        'id': 23261,
        'sn_ativo': 'true',
        'valor_recursado': '520.14',
        'dt_recurso': '2026-08-20',
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


def test_gera_pdf_com_layout_de_recurso():
    conteudo = gerar_pdf_recurso_glosa(_card_recurso())

    assert conteudo.startswith(b'%PDF-')
    assert len(conteudo) > TAMANHO_MINIMO_PDF


def test_pdf_exige_ao_menos_um_recurso_registrado():
    card = _card_recurso()
    for item in card['pacientes'][0]['itens']:
        item['registro_recusa'] = None

    with pytest.raises(
        ValueError,
        match='não possui recursos registrados',
    ):
        gerar_pdf_recurso_glosa(card)
