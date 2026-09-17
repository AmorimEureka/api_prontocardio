# ruff: noqa: PLR2004

from datetime import date
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
                'valor_vencido': Decimal('500000'),
                'valor_corrente': Decimal('25000'),
                'vencimento_mais_antigo': date(2026, 6, 9),
                'novos_vencidos_7d': Decimal('12000'),
                'titulos_vencidos': 4,
                'titulos_correntes': 2,
            }
        ]
    ])

    resultado = contas_pagar._consultar_oracle(oracle)

    assert resultado[0]['dias_atraso'] == 100
    assert resultado[0]['valor_vencido'] == Decimal('500000.00')
    assert resultado[0]['titulos_vencidos'] == 4


def test_consulta_oracle_ignora_fornecedor_sem_codigo():
    oracle = FakeSession([
        [
            {
                'codigo_fornecedor': None,
                'nome_fornecedor': 'Fornecedor sem código',
                'valor_vencido': Decimal('10'),
                'valor_corrente': Decimal('0'),
                'vencimento_mais_antigo': None,
                'novos_vencidos_7d': Decimal('0'),
                'titulos_vencidos': 1,
                'titulos_correntes': 0,
            }
        ]
    ])

    assert contas_pagar._consultar_oracle(oracle) == []
    assert 'codigo_do_fornecedor IS NOT NULL' in oracle.executions[0][0]


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
