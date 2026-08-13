import json
from datetime import date, datetime

from openpyxl import Workbook

from scripts.importar_tiss_ans import (
    carregar_terminologia,
    carregar_terminologia_fhir,
)


def test_carrega_tabela_38_da_planilha_oficial(tmp_path):
    arquivo = tmp_path / 'tuss.xlsx'
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = 'Tab 38'
    worksheet.append([])
    worksheet.append([])
    worksheet.append([])
    worksheet.append(['Tabela 38'])
    worksheet.append([])
    worksheet.append(
        [
            'Código do Termo',
            'Termo',
            'Data de início de vigência',
            'Data de fim de vigência',
            'Data de fim de implantação',
        ]
    )
    worksheet.append(
        [
            1714,
            'VALOR DO SERVIÇO SUPERIOR AO VALOR DE TABELA',
            datetime(2006, 11, 16),
            None,
            date(2006, 11, 16),
        ]
    )
    workbook.save(arquivo)

    itens = carregar_terminologia(arquivo, '202505')

    assert itens == [
        {
            'codigo_termo': '1714',
            'termo': 'VALOR DO SERVIÇO SUPERIOR AO VALOR DE TABELA',
            'dt_inicio_vigencia': date(2006, 11, 16),
            'dt_fim_vigencia': None,
            'dt_fim_implantacao': date(2006, 11, 16),
            'fonte': 'ANS - Padrão TISS, Tabela 38, versão 202505',
            'pagina_pdf': 0,
        }
    ]


def test_carrega_code_system_fhir_historico(tmp_path):
    arquivo = tmp_path / 'tuss-38.json'
    arquivo.write_text(
        json.dumps(
            {
                'resourceType': 'CodeSystem',
                'version': '202309',
                'concept': [
                    {
                        'code': '1714',
                        'display': (
                            'VALOR DO SERVIÇO SUPERIOR AO VALOR DE TABELA'
                        ),
                    },
                    {
                        'code': 'grupo',
                        'display': '',
                        'concept': [
                            {
                                'code': '1702',
                                'display': (
                                    'COBRANÇA DE PROCEDIMENTO EM DUPLICIDADE'
                                ),
                            }
                        ],
                    },
                ],
            }
        ),
        encoding='utf-8',
    )

    itens = carregar_terminologia_fhir(arquivo, '202309')

    assert [item['codigo_termo'] for item in itens] == ['1714', '1702']
    assert itens[0] == {
        'codigo_termo': '1714',
        'termo': 'VALOR DO SERVIÇO SUPERIOR AO VALOR DE TABELA',
        'dt_inicio_vigencia': None,
        'dt_fim_vigencia': None,
        'dt_fim_implantacao': None,
        'fonte': 'ANS - CodeSystem FHIR TUSS 38, versão 202309',
        'pagina_pdf': 0,
    }
