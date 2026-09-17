# ruff: noqa: PLR2004

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from app_prontocardio.routers import contas_pagar


class FakeMappings:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return FakeMappings(self.rows)

    def scalar_one(self):
        return self.rows[0]

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.executions = []
        self.commits = 0

    def execute(self, statement, params=None):
        self.executions.append((str(statement), params))
        return FakeResult(next(self.responses, []))

    def commit(self):
        self.commits += 1


def test_consulta_oracle_converte_saldos_e_atraso(monkeypatch):
    monkeypatch.setattr(
        contas_pagar, 'date', SimpleNamespace(today=lambda: date(2026, 9, 17))
    )
    oracle = FakeSession([
        [
            {
                'codigo_fornecedor': 10,
                'nome_fornecedor': 'Fornecedor crítico',
                'codigo_parcela_pk': 99,
                'codigo_contas_pagar': 88,
                'numero_documento': 'NF-10',
                'descricao_conta': 'Medicamentos',
                'numero_parcela': 1,
                'data_lancamento': datetime(2026, 5, 20, 0, 0),
                'data_emissao': datetime(2026, 5, 18, 0, 0),
                'valor_duplicata': Decimal('500000'),
                'data_vencimento': datetime(2026, 6, 9, 0, 0),
                'tipo_quitacao': 'parcialmente pago',
                'valor_honrado_oracle': Decimal('100000'),
            }
        ]
    ])

    titulos = contas_pagar._consultar_titulos_oracle(oracle, [10])
    resultado = contas_pagar._agrupar_fornecedores(
        titulos,
        {
            99: [{
                'valor_pago': Decimal('50000'),
            }]
        },
    )

    assert resultado[0]['dias_atraso'] == 100
    assert resultado[0]['vencimento_mais_antigo'] == date(2026, 6, 9)
    assert resultado[0]['titulos'][0]['data_lancamento'] == date(2026, 5, 20)
    assert resultado[0]['titulos'][0]['data_emissao'] == date(2026, 5, 18)
    assert resultado[0]['valor_total'] == Decimal('500000.00')
    assert resultado[0]['valor_total_honrado'] == Decimal('150000.00')
    assert resultado[0]['valor_total_vencido'] == Decimal('350000.00')
    assert resultado[0]['saldo_a_pagar'] == Decimal('350000.00')
    assert resultado[0]['total_dias_vencidos'] == 100


def test_consulta_oracle_ignora_fornecedor_sem_codigo():
    oracle = FakeSession([
        [
            {
                'codigo_fornecedor': None,
                'nome_fornecedor': 'Fornecedor sem código',
                'codigo_parcela_pk': 1,
            }
        ]
    ])

    assert contas_pagar._consultar_titulos_oracle(oracle, [10]) == []
    assert 'codigo_do_fornecedor IS NOT NULL' in oracle.executions[0][0]


def test_titulos_do_fornecedor_sao_ordenados_por_maior_atraso(monkeypatch):
    monkeypatch.setattr(
        contas_pagar, 'date', SimpleNamespace(
            today=lambda: date(2026, 9, 17),
            max=date.max,
        )
    )
    base = {
        'codigo_fornecedor': 10,
        'nome_fornecedor': 'Fornecedor crítico',
        'codigo_contas_pagar': 88,
        'numero_documento': 'NF-10',
        'descricao_conta': 'Medicamentos',
        'numero_parcela': 1,
        'valor_total': Decimal('100'),
        'tipo_quitacao': 'previsto',
        'valor_honrado_oracle': Decimal('0'),
    }
    titulos = [
        {**base, 'codigo_parcela': 1, 'data_vencimento': date(2026, 8, 1)},
        {**base, 'codigo_parcela': 2, 'data_vencimento': date(2026, 5, 1)},
        {**base, 'codigo_parcela': 3, 'data_vencimento': date(2026, 7, 1)},
    ]

    resultado = contas_pagar._agrupar_fornecedores(titulos, {})

    assert [
        titulo['codigo_parcela'] for titulo in resultado[0]['titulos']
    ] == [2, 3, 1]
    assert [
        titulo['dias_vencidos'] for titulo in resultado[0]['titulos']
    ] == sorted(
        [titulo['dias_vencidos'] for titulo in resultado[0]['titulos']],
        reverse=True,
    )


def test_pagamento_titulo_pode_ser_incluido_atualizado_e_excluido():
    usuario = SimpleNamespace(id=7)
    payload = contas_pagar.PagamentoTituloInput(
        data_pagamento='2026-09-17',
        valor_pago='1250.50',
        banco='Banco Pronto',
        agencia='0001',
        numero_conta='12345-6',
        observacao='Pagamento parcial',
    )
    inclusao = FakeSession([[31]])

    resposta = contas_pagar.registrar_pagamento_titulo(
        10, 99, payload, usuario, inclusao
    )

    assert resposta['id'] == 31
    assert inclusao.executions[0][1]['parcela'] == 99
    assert inclusao.executions[0][1]['banco'] == 'Banco Pronto'
    assert inclusao.executions[0][1]['agencia'] == '0001'
    assert inclusao.executions[0][1]['numero_conta'] == '12345-6'
    assert inclusao.commits == 1

    atualizacao = FakeSession([[31]])
    resposta = contas_pagar.atualizar_pagamento_titulo(
        10, 99, 31, payload, usuario, atualizacao
    )
    assert resposta['detail'] == 'Pagamento atualizado com sucesso.'
    assert atualizacao.commits == 1

    exclusao = FakeSession([[31]])
    resposta = contas_pagar.excluir_pagamento_titulo(
        10, 99, 31, usuario, exclusao
    )
    assert resposta.status_code == 204
    assert exclusao.commits == 1


def test_criticidade_e_atualizada_sem_sobrescrever_tratamento():
    session = FakeSession([[]])

    resposta = contas_pagar.atualizar_criticidade_fornecedor(
        10,
        contas_pagar.CriticidadeFornecedorInput(critico=True),
        SimpleNamespace(id=7),
        session,
    )

    sql, params = session.executions[0]
    assert 'critico = EXCLUDED.critico' in sql
    assert 'pagamento_imediato = EXCLUDED' not in sql
    assert params == {'codigo': 10, 'critico': True, 'usuario_id': 7}
    assert resposta['detail'] == 'Fornecedor marcado como crítico.'


def test_prioridade_ordena_criticos_e_depois_maior_atraso():
    fornecedores = [
        {
            'codigo': 1,
            'critico': False,
            'dias_atraso': 500,
            'valor_total_vencido': Decimal('1'),
        },
        {
            'codigo': 2,
            'critico': True,
            'dias_atraso': 30,
            'valor_total_vencido': Decimal('1'),
        },
        {
            'codigo': 3,
            'critico': True,
            'dias_atraso': 90,
            'valor_total_vencido': Decimal('1'),
        },
        {
            'codigo': 4,
            'critico': False,
            'dias_atraso': 100,
            'valor_total_vencido': Decimal('1'),
        },
    ]

    fornecedores.sort(key=contas_pagar._chave_prioridade_fornecedor)

    assert [item['codigo'] for item in fornecedores] == [3, 2, 1, 4]


def test_salvar_tratamento_faz_upsert_com_usuario():
    session = FakeSession([[]])
    payload = contas_pagar.TratamentoInput(
        critico=True,
        pagamento_imediato='100000',
        status='NEGOCIACAO',
        responsavel='Ana',
    )

    resposta = contas_pagar.salvar_tratamento(
        10, payload, SimpleNamespace(id=7), session
    )

    assert resposta['detail'] == 'Tratamento salvo com sucesso.'
    assert session.executions[0][1]['codigo'] == 10
    assert session.executions[0][1]['usuario'] == 7
    assert session.commits == 1


def test_excluir_remove_apenas_tratamento_operacional():
    session = FakeSession([[]])

    resposta = contas_pagar.excluir_tratamento(
        10, SimpleNamespace(id=7), session
    )

    sql, params = session.executions[0]
    assert 'contas_pagar_tratamentos' in sql
    assert 'HPC_V_CONTAS_A_PAGAR' not in sql
    assert params == {'codigo': 10}
    assert resposta.status_code == 204
