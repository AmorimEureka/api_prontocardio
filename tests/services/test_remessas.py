from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.dialects import oracle

from app_prontocardio.models import RecebimentoRemessa, RemessaFinanceira
from app_prontocardio.services import remessas


class OracleFake:
    @staticmethod
    def execute(_query):
        raise AssertionError('A consulta deveria ter sido substituída.')


def test_sincroniza_total_e_estado_financeiro_de_remessa_existente(
    session,
    monkeypatch,
):
    remessa = RemessaFinanceira(
        cd_remessa=987,
        convenio='Convênio Teste',
        cnpj_convenio='98765432000110',
        valor_total=Decimal('120.00'),
    )
    remessa.data_registro = datetime(2026, 7, 1, 8, 0)
    session.add(remessa)
    session.flush()
    recebimento = RecebimentoRemessa(
        cd_remessa=remessa.cd_remessa,
        conciliacao_id=1,
        numero_nfse='NFSE-1',
        data_recebimento=date(2026, 7, 2),
        valor_recebido=Decimal('100.00'),
        usuario_id=1,
        conta_bancaria_id=7,
    )
    recebimento.data_registro = datetime(2026, 7, 2, 8, 0)
    session.add(recebimento)
    session.commit()

    monkeypatch.setattr(
        remessas,
        'consultar_totais_remessas_hpc',
        lambda _session, _codigos: {987: Decimal('100.00')},
    )
    remessas.sincronizar_totais_remessas_financeiras(
        session,
        OracleFake(),
    )

    assert remessa.valor_total == Decimal('100.00')
    assert remessa.recebimento_integral is True
    assert recebimento.recebimento_integral is True

    monkeypatch.setattr(
        remessas,
        'consultar_totais_remessas_hpc',
        lambda _session, _codigos: {987: Decimal('140.00')},
    )
    remessas.sincronizar_totais_remessas_financeiras(
        session,
        OracleFake(),
    )

    assert remessa.valor_total == Decimal('140.00')
    assert remessa.recebimento_integral is False
    assert recebimento.recebimento_integral is False


def test_consulta_total_da_remessa_usa_vl_total_registro():
    capturado = {}

    class Resultado:
        @staticmethod
        def all():
            return []

    class OracleSession:
        @staticmethod
        def execute(query):
            capturado['query'] = query
            return Resultado()

    remessas.consultar_totais_remessas_hpc(OracleSession(), {987})

    sql = str(capturado['query'].compile(dialect=oracle.dialect()))
    assert 'vl_total_registro' in sql
    assert 'vl_total_conta' not in sql


def test_retorna_total_hpc_mesmo_sem_snapshot_financeiro(
    session,
    monkeypatch,
):
    monkeypatch.setattr(
        remessas,
        'consultar_totais_remessas_hpc',
        lambda _session, _codigos: {555: Decimal('75.00')},
    )

    totais = remessas.sincronizar_totais_remessas_financeiras(
        session,
        OracleFake(),
        {555},
    )

    assert totais == {555: Decimal('75.00')}
