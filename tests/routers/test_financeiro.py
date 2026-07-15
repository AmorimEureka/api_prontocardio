from datetime import date, datetime
from decimal import Decimal
from http import HTTPStatus
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import insert, select
from sqlalchemy.dialects import oracle

from app_prontocardio.models import (
    ConciliacaoFaturamento,
    ConciliacaoFaturamentoRemessa,
    LancamentoExtratoBancario,
    NfseXml,
    RecebimentoRemessa,
    RegistroGlosa,
    RemessaFinanceira,
    TipoAtendimento,
)
from app_prontocardio.routers import app_glosas, financeiro
from app_prontocardio.schema import (
    ConciliacaoFaturamentoCreate,
    RecebimentoRemessaCreate,
    RegistroGlosaCreate,
)

CD_REMESSA_TESTE = 987
CONTA_BANCARIA_TESTE = 7
ITENS_ANALITICOS_TESTE = 2


def criar_nfse(
    session,
    row_hash='nfse-1',
    valor='100.00',
    numero_nfse='12345',
):
    session.execute(
        insert(NfseXml).values(
            row_hash=row_hash,
            data_hora=datetime(2026, 7, 10, 10, 0),
            numero_nfse=numero_nfse,
            prestador_cnpj='12.345.678/0001-90',
            prestador_razao_social='Hospital Prontocardio',
            tomador_cnpj='98.765.432/0001-10',
            tomador_razao_social='Convenio Teste',
            valor_pis='1.00',
            valor_cofins='2.00',
            valor_csll='3.00',
            valor_ir='4.00',
            outras_retencoes='5.00',
            valor_liquido_nfse=valor,
            cancelamento_codigo=None,
        )
    )
    session.commit()


def payload_conciliacao(row_hash='nfse-1', **overrides):
    payload = {
        'nfse_row_hash': row_hash,
        'processo_recebimento': 'PROC-2026-001',
        'data_previsao_recebimento': '2026-08-10',
        'remessas': [
            {
                'cd_remessa': CD_REMESSA_TESTE,
                'sn_glosado': True,
                'valor_glosado': '20.00',
            }
        ],
    }
    payload.update(overrides)
    return payload


def remessas_hpc(*_args, **_kwargs):
    return [
        {
            'cd_remessa': CD_REMESSA_TESTE,
            'cd_convenio': 10,
            'convenio': 'Convenio Teste',
            'cnpj_convenio': '98765432000110',
            'valor_total': '120.00',
        }
    ]


def itens_remessas_hpc(*_args, **_kwargs):
    return [
        {
            'codigo_paciente': 1,
            'nm_paciente': 'Paciente Um',
            'cd_remessa': CD_REMESSA_TESTE,
            'cd_atendimento': 101,
            'conta': 1001,
            'cd_lancamento': 1,
            'cd_prestador': 11,
            'cd_convenio': 10,
            'tp_atendimento': TipoAtendimento.AMBULATORIO.value,
            'procedimento': 'PROC-1',
            'convenio': 'Convenio Teste',
            'guia': 'GUIA-1',
            'prestador': 'Prestador Um',
            'data_atendimento': datetime(2026, 6, 1),
            'valor': Decimal('60.00'),
            'qtd_registro': Decimal('1.00'),
            'descricao_item': 'Item analitico um',
            'data_alta': datetime(2026, 6, 1, 12, 0),
            'data_lancamento': datetime(2026, 6, 1, 8, 30),
        },
        {
            'codigo_paciente': 2,
            'nm_paciente': 'Paciente Dois',
            'cd_remessa': CD_REMESSA_TESTE,
            'cd_atendimento': 102,
            'conta': 1002,
            'cd_lancamento': 2,
            'cd_prestador': 12,
            'cd_convenio': 10,
            'tp_atendimento': TipoAtendimento.INTERNACAO.value,
            'procedimento': 'PROC-2',
            'convenio': 'Convenio Teste',
            'guia': 'GUIA-2',
            'prestador': 'Prestador Dois',
            'data_atendimento': datetime(2026, 6, 2),
            'valor': Decimal('60.00'),
            'qtd_registro': Decimal('2.00'),
            'descricao_item': 'Item analitico dois',
            'data_alta': datetime(2026, 6, 2, 12, 0),
            'data_lancamento': datetime(2026, 6, 2, 9, 0),
        },
    ]


def criar_recurso_aberto(
    session,
    cd_remessa=CD_REMESSA_TESTE,
    valor_recursado='20.00',
    **overrides,
):
    values = {
        'codigo_paciente': 1,
        'nm_paciente': 'Paciente Teste',
        'cd_remessa': cd_remessa,
        'cd_atendimento': 2,
        'conta': 3,
        'cd_prestador': 4,
        'cd_convenio': 10,
        'tp_atendimento': TipoAtendimento.AMBULATORIO,
        'procedimento': 'PROC',
        'convenio': 'Convenio Teste',
        'guia': 'GUIA',
        'prestador': 'Prestador Teste',
        'data_atendimento': datetime(2026, 6, 1),
        'valor': Decimal('120.00'),
        'processo_controle_fatura_gab': 'GAB-1',
        'processo_recurso': 'REC-1',
        'data_glosa': date(2026, 6, 2),
        'motivo_glosa': 'Motivo',
        'descricao_glosa': 'Descricao',
        'qtd_recursado': Decimal('1.00'),
        'valor_recursado': Decimal(valor_recursado),
        'dt_recurso': date(2026, 6, 3),
        'dt_pagamento': date(2026, 6, 2),
        'dt_recebimento': None,
        'valor_recebido': None,
        'qtd_recebida': None,
        'observacao_recebimento': None,
        'sn_glosado': 'true',
        'sn_ativo': 'true',
    }
    values.update(overrides)
    registro = RegistroGlosa(**values)
    registro.data_criacao = datetime(2026, 6, 3, 10, 0)
    session.add(registro)
    session.commit()
    return registro


def criar_conciliacao_anterior_com_glosa(
    session,
    usuario_id,
    cd_remessa=CD_REMESSA_TESTE,
    valor_total='120.00',
    valor_glosado='20.00',
):
    conciliacao = ConciliacaoFaturamento(
        nfse_row_hash='nfse-anterior',
        numero_nfse='NFSE-ANTERIOR',
        cnpj_convenio='98765432000110',
        convenio='Convenio Teste',
        valor_nfse=Decimal(valor_total) - Decimal(valor_glosado),
        impostos=Decimal('0.00'),
        processo_recebimento='PROC-ANTERIOR',
        data_previsao_recebimento=date(2026, 6, 30),
        usuario_id=usuario_id,
        data_recebimento=date(2026, 6, 10),
        conta_bancaria_id=CONTA_BANCARIA_TESTE,
    )
    conciliacao.data_criacao = datetime(2026, 6, 10, 10, 0)
    session.add(conciliacao)
    session.flush()
    remessa = ConciliacaoFaturamentoRemessa(
        conciliacao_id=conciliacao.id,
        cd_remessa=cd_remessa,
        convenio='Convenio Teste',
        cnpj_convenio='98765432000110',
        valor_total=Decimal(valor_total),
        sn_glosado='true',
        valor_glosado=Decimal(valor_glosado),
        tp_conciliacao='faturamento',
    )
    session.add(remessa)
    session.flush()
    remessa_financeira = RemessaFinanceira(
        cd_remessa=cd_remessa,
        convenio='Convenio Teste',
        cnpj_convenio='98765432000110',
        valor_total=Decimal(valor_total),
        recebimento_integral=False,
    )
    remessa_financeira.data_registro = datetime(2026, 6, 10, 10, 0)
    session.add(remessa_financeira)
    session.flush()
    recebimento = RecebimentoRemessa(
        cd_remessa=cd_remessa,
        conciliacao_id=conciliacao.id,
        numero_nfse=conciliacao.numero_nfse,
        data_recebimento=date(2026, 6, 10),
        valor_recebido=Decimal(valor_total) - Decimal(valor_glosado),
        usuario_id=usuario_id,
        conta_bancaria_id=CONTA_BANCARIA_TESTE,
        recebimento_integral=False,
    )
    recebimento.data_registro = datetime(2026, 6, 10, 10, 0)
    session.add(recebimento)
    session.commit()
    return remessa


def configurar_oracle_fake(monkeypatch):
    monkeypatch.setattr(
        financeiro,
        '_consultar_remessas_hpc',
        remessas_hpc,
    )
    monkeypatch.setattr(
        financeiro,
        '_consultar_convenios_hpc',
        lambda _session: {
            '98765432000110': {
                'cd_convenio': 10,
                'cnpj_convenio': '98765432000110',
                'convenio': 'Convenio Teste',
            }
        },
    )
    monkeypatch.setattr(
        financeiro,
        '_consultar_itens_remessas_hpc',
        itens_remessas_hpc,
    )


def payload_tratativa(registro, processo, valor):
    return RegistroGlosaCreate(
        codigo_paciente=registro.codigo_paciente,
        nm_paciente=registro.nm_paciente,
        cd_remessa=registro.cd_remessa,
        cd_atendimento=registro.cd_atendimento,
        conta=registro.conta,
        cd_lancamento=registro.cd_lancamento,
        cd_prestador=registro.cd_prestador,
        cd_convenio=registro.cd_convenio,
        tp_atendimento=registro.tp_atendimento,
        procedimento=registro.procedimento,
        convenio=registro.convenio,
        guia=registro.guia,
        prestador=registro.prestador,
        data_atendimento=registro.data_atendimento,
        valor=registro.valor,
        processo_controle_fatura_gab=(
            registro.processo_controle_fatura_gab
        ),
        processo_recurso=processo,
        data_glosa=registro.data_glosa,
        motivo_glosa='Glosa analisada',
        descricao_glosa='Item identificado pelo setor de glosas',
        qtd_registro=registro.qtd_registro,
        qtd_recursado=Decimal('1.00'),
        valor_recursado=Decimal(valor),
        dt_recurso=registro.data_glosa,
        dt_pagamento=registro.data_glosa,
        sn_glosado='true',
    )


class OracleComContaFake:
    @staticmethod
    def scalar(_query):
        return SimpleNamespace(cd_con_cor=7)


def test_lista_apenas_nfse_nao_conciliada(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session)
    criar_nfse(session, row_hash='nfse-2')
    configurar_oracle_fake(monkeypatch)
    response = financeiro.consultar_nfses_pendentes(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        limit=100,
        offset=0,
    )

    assert response['total'] == 1
    assert response['valor_total_nfse'] == Decimal('100.00')
    assert len(response['notas']) == 1
    assert response['notas'][0] == {
        'row_hash': 'nfse-2',
        'numero_nfse': '12345',
        'data_emissao': datetime(2026, 7, 10, 10, 0),
        'convenio': 'Convenio Teste',
        'cnpj_convenio': '98765432000110',
        'impostos': Decimal('15.00'),
        'valor_nfse': Decimal('100.00'),
    }

    response = financeiro.conciliar_faturamento(
        payload=ConciliacaoFaturamentoCreate(**payload_conciliacao()),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )

    assert response['total_remessas'] == Decimal('120.00')
    assert response['total_glosas'] == Decimal('20.00')
    assert session.scalar(select(ConciliacaoFaturamento)) is not None
    remessa = session.scalar(select(ConciliacaoFaturamentoRemessa))
    assert remessa.sn_glosado == 'true'
    assert str(remessa.valor_glosado) == '20.00'
    registros_glosa = session.scalars(
        select(RegistroGlosa)
        .where(RegistroGlosa.conciliacao_remessa_id == remessa.id)
        .order_by(RegistroGlosa.cd_lancamento)
    ).all()
    assert len(registros_glosa) == ITENS_ANALITICOS_TESTE
    assert [registro.conta for registro in registros_glosa] == [1001, 1002]
    assert [registro.cd_lancamento for registro in registros_glosa] == [1, 2]
    assert [registro.qtd_registro for registro in registros_glosa] == [
        Decimal('1.00'),
        Decimal('2.00'),
    ]
    assert all(
        registro.processo_recurso is None for registro in registros_glosa
    )
    assert all(
        registro.valor_recursado is None for registro in registros_glosa
    )
    assert registros_glosa[0].valor_glosa_origem == Decimal('20.00')
    assert registros_glosa[0].valor_glosa_pendente == Decimal('20.00')

    response = financeiro.consultar_nfses_pendentes(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        limit=100,
        offset=0,
    )
    assert response == {
        'notas': [],
        'total': 0,
        'valor_total_nfse': Decimal('0.00'),
        'limit': 100,
        'offset': 0,
    }


def test_follow_up_exibe_somente_glosas_pendentes_da_conciliacao(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session)
    configurar_oracle_fake(monkeypatch)
    financeiro.conciliar_faturamento(
        payload=ConciliacaoFaturamentoCreate(**payload_conciliacao()),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )
    registros_glosa = session.scalars(
        select(RegistroGlosa).order_by(RegistroGlosa.cd_lancamento)
    ).all()

    follow_up = financeiro.consultar_follow_up_glosas(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        limit=20,
        offset=0,
    )

    assert follow_up['total'] == 1
    assert follow_up['valor_total_glosado'] == Decimal('20.00')
    assert follow_up['valor_total_pendente'] == Decimal('20.00')
    card = follow_up['cards'][0]
    assert card['cd_remessa'] == CD_REMESSA_TESTE
    assert card['numero_nfse'] == '12345'
    assert card['valor_remessa'] == Decimal('120.00')
    assert card['valor_glosado'] == Decimal('20.00')
    assert len(card['pacientes']) == ITENS_ANALITICOS_TESTE
    itens = [
        item
        for paciente in card['pacientes']
        for item in paciente['itens']
    ]
    primeiro_item = next(
        item for item in itens if item['cd_lancamento'] == 1
    )
    assert primeiro_item['descricao'] == 'Item analitico um'
    assert primeiro_item['dt_alta'] == datetime(2026, 6, 1, 12, 0)
    assert primeiro_item['dt_lancamento'] == datetime(2026, 6, 1, 8, 30)

    app_glosas.editar_glosa(
        registros_glosa[0].id,
        payload_tratativa(registros_glosa[0], 'REC-ITEM-1', '10.00'),
        usuario_atual=usuario_teste,
        session=session,
    )
    follow_up = financeiro.consultar_follow_up_glosas(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q='987',
        limit=20,
        offset=0,
    )
    assert follow_up['valor_total_pendente'] == Decimal('10.00')

    with pytest.raises(HTTPException) as error:
        app_glosas.editar_glosa(
            registros_glosa[1].id,
            payload_tratativa(registros_glosa[1], 'REC-ITEM-2', '11.00'),
            usuario_atual=usuario_teste,
            session=session,
        )
    assert error.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    app_glosas.editar_glosa(
        registros_glosa[1].id,
        payload_tratativa(registros_glosa[1], 'REC-ITEM-2', '10.00'),
        usuario_atual=usuario_teste,
        session=session,
    )
    follow_up = financeiro.consultar_follow_up_glosas(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        limit=20,
        offset=0,
    )
    assert follow_up['cards'] == []


def test_follow_up_sincroniza_glosa_legada_sem_registros_analiticos(
    session,
    usuario_teste,
    monkeypatch,
):
    configurar_oracle_fake(monkeypatch)
    vinculo = criar_conciliacao_anterior_com_glosa(
        session,
        usuario_teste.id,
    )
    assert session.scalars(
        select(RegistroGlosa).where(
            RegistroGlosa.conciliacao_remessa_id == vinculo.id
        )
    ).all() == []

    follow_up = financeiro.consultar_follow_up_glosas(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        limit=20,
        offset=0,
    )

    assert follow_up['total'] == 1
    assert follow_up['cards'][0]['cd_remessa'] == CD_REMESSA_TESTE
    assert len(follow_up['cards'][0]['pacientes']) == ITENS_ANALITICOS_TESTE
    registros = session.scalars(
        select(RegistroGlosa).where(
            RegistroGlosa.conciliacao_remessa_id == vinculo.id
        )
    ).all()
    assert len(registros) == ITENS_ANALITICOS_TESTE

    financeiro.consultar_follow_up_glosas(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        limit=20,
        offset=0,
    )
    assert len(
        session.scalars(
            select(RegistroGlosa).where(
                RegistroGlosa.conciliacao_remessa_id == vinculo.id
            )
        ).all()
    ) == ITENS_ANALITICOS_TESTE


def test_totaliza_valor_de_todas_nfses_independente_da_paginacao(
    session,
    usuario_teste,
    monkeypatch,
):
    total_nfses = 2
    criar_nfse(session, valor='100.00', numero_nfse='12345')
    criar_nfse(
        session,
        row_hash='nfse-2',
        valor='50.25',
        numero_nfse='67890',
    )
    configurar_oracle_fake(monkeypatch)

    response = financeiro.consultar_nfses_pendentes(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        limit=1,
        offset=0,
    )

    assert response['total'] == total_nfses
    assert response['valor_total_nfse'] == Decimal('150.25')
    assert len(response['notas']) == 1


def test_conciliacao_glosada_exige_itens_analiticos(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session)
    configurar_oracle_fake(monkeypatch)
    monkeypatch.setattr(
        financeiro,
        '_consultar_itens_remessas_hpc',
        lambda *_args, **_kwargs: [],
    )

    with pytest.raises(HTTPException) as error:
        financeiro.conciliar_faturamento(
            payload=ConciliacaoFaturamentoCreate(**payload_conciliacao()),
            usuario_atual=usuario_teste,
            session_postgres=session,
            session_oracle=object(),
        )

    assert error.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert 'itens analiticos no Oracle' in error.value.detail
    assert session.scalar(select(ConciliacaoFaturamento)) is None


def test_lista_conciliacao_com_remessa_sem_recebimento(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session)
    configurar_oracle_fake(monkeypatch)
    financeiro.conciliar_faturamento(
        payload=ConciliacaoFaturamentoCreate(**payload_conciliacao()),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )

    response = financeiro.consultar_conciliacoes_sem_recebimento(
        usuario_atual=usuario_teste,
        session=session,
        q='987',
        limit=100,
        offset=0,
    )

    assert response['total'] == 1
    assert response['total_remessas_sem_recebimento'] == 1
    assert response['valor_total_pendente'] == Decimal('100.00')
    conciliacao = response['conciliacoes'][0]
    assert conciliacao['numero_nfse'] == '12345'
    assert conciliacao['situacao'] == 'sem_recebimento'
    assert conciliacao['quantidade_remessas'] == 1
    assert conciliacao['quantidade_remessas_sem_recebimento'] == 1
    assert conciliacao['valor_total_remessas'] == Decimal('120.00')
    assert conciliacao['valor_total_glosas'] == Decimal('20.00')
    assert conciliacao['valor_previsto_recebimento'] == Decimal('100.00')
    assert conciliacao['valor_recebido'] == Decimal('0.00')
    assert conciliacao['valor_pendente'] == Decimal('100.00')
    assert conciliacao['remessas'] == [
        {
            'cd_remessa': CD_REMESSA_TESTE,
            'tp_conciliacao': 'faturamento',
            'valor_remessa': Decimal('120.00'),
            'valor_glosado': Decimal('20.00'),
            'valor_pendente': Decimal('100.00'),
        }
    ]


def test_conciliacao_recebida_nao_aparece_na_fila_sem_recebimento(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session)
    configurar_oracle_fake(monkeypatch)
    financeiro.conciliar_faturamento(
        payload=ConciliacaoFaturamentoCreate(
            **payload_conciliacao(
                data_recebimento='2026-07-10',
                conta_bancaria_id=CONTA_BANCARIA_TESTE,
            )
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=OracleComContaFake(),
    )

    response = financeiro.consultar_conciliacoes_sem_recebimento(
        usuario_atual=usuario_teste,
        session=session,
        q=None,
        limit=100,
        offset=0,
    )

    assert response == {
        'conciliacoes': [],
        'total': 0,
        'total_remessas_sem_recebimento': 0,
        'valor_total_pendente': Decimal('0.00'),
        'limit': 100,
        'offset': 0,
    }


def test_lista_apenas_remessa_pendente_em_conciliacao_parcial(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session)
    configurar_oracle_fake(monkeypatch)
    financeiro.conciliar_faturamento(
        payload=ConciliacaoFaturamentoCreate(**payload_conciliacao()),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )
    conciliacao = session.scalar(select(ConciliacaoFaturamento))
    remessa_recebida = session.get(RemessaFinanceira, CD_REMESSA_TESTE)
    recebimento = RecebimentoRemessa(
        cd_remessa=CD_REMESSA_TESTE,
        conciliacao_id=conciliacao.id,
        numero_nfse=conciliacao.numero_nfse,
        data_recebimento=date(2026, 7, 10),
        valor_recebido=Decimal('100.00'),
        usuario_id=usuario_teste.id,
        conta_bancaria_id=CONTA_BANCARIA_TESTE,
        recebimento_integral=False,
    )
    recebimento.data_registro = datetime(2026, 7, 10, 10, 0)
    session.add(recebimento)
    remessa_recebida.recebimento_integral = False

    cd_remessa_pendente = 988
    session.add(
        ConciliacaoFaturamentoRemessa(
            conciliacao_id=conciliacao.id,
            cd_remessa=cd_remessa_pendente,
            convenio='Convenio Teste',
            cnpj_convenio='98765432000110',
            valor_total=Decimal('50.00'),
            sn_glosado='true',
            valor_glosado=Decimal('10.00'),
            tp_conciliacao='faturamento',
        )
    )
    remessa_pendente = RemessaFinanceira(
        cd_remessa=cd_remessa_pendente,
        convenio='Convenio Teste',
        cnpj_convenio='98765432000110',
        valor_total=Decimal('50.00'),
        recebimento_integral=False,
    )
    remessa_pendente.data_registro = datetime(2026, 7, 10, 10, 0)
    session.add(remessa_pendente)
    session.commit()

    response = financeiro.consultar_conciliacoes_sem_recebimento(
        usuario_atual=usuario_teste,
        session=session,
        q=str(cd_remessa_pendente),
        limit=100,
        offset=0,
    )

    assert response['total'] == 1
    item = response['conciliacoes'][0]
    quantidade_remessas = 2
    assert item['situacao'] == 'recebimento_parcial'
    assert item['quantidade_remessas'] == quantidade_remessas
    assert item['quantidade_remessas_sem_recebimento'] == 1
    assert item['valor_recebido'] == Decimal('100.00')
    assert item['valor_pendente'] == Decimal('40.00')
    assert [remessa['cd_remessa'] for remessa in item['remessas']] == [988]

    response_remessa_recebida = (
        financeiro.consultar_conciliacoes_sem_recebimento(
            usuario_atual=usuario_teste,
            session=session,
            q=str(CD_REMESSA_TESTE),
            limit=100,
            offset=0,
        )
    )
    assert response_remessa_recebida['total'] == 0


def test_conciliacao_integralmente_glosada_nao_gera_recebimento_pendente(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='0.00')
    configurar_oracle_fake(monkeypatch)
    financeiro.conciliar_faturamento(
        payload=ConciliacaoFaturamentoCreate(
            **payload_conciliacao(
                remessas=[
                    {
                        'cd_remessa': CD_REMESSA_TESTE,
                        'sn_glosado': True,
                        'valor_glosado': '120.00',
                    }
                ]
            )
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )

    response = financeiro.consultar_conciliacoes_sem_recebimento(
        usuario_atual=usuario_teste,
        session=session,
        q=None,
        limit=100,
        offset=0,
    )

    assert response['total'] == 0
    assert response['valor_total_pendente'] == Decimal('0.00')


def test_usa_razao_social_do_tomador_quando_convenio_nao_for_encontrado(
    session,
):
    criar_nfse(session)
    nota = session.get(NfseXml, 'nfse-1')

    response = financeiro._nota_publica(nota, convenio=None)

    assert response['convenio'] == 'Convenio Teste'


def test_rejeita_conciliacao_com_totais_divergentes(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session)
    configurar_oracle_fake(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        financeiro.conciliar_faturamento(
            payload=ConciliacaoFaturamentoCreate(
                **payload_conciliacao(
                    remessas=[
                        {
                            'cd_remessa': 987,
                            'sn_glosado': False,
                            'valor_glosado': '0.00',
                        }
                    ]
                )
            ),
            usuario_atual=usuario_teste,
            session_postgres=session,
            session_oracle=object(),
        )

    assert exc_info.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert exc_info.value.detail == financeiro.MENSAGEM_VALORES_DIVERGENTES


def test_rejeita_glosa_maior_que_remessa(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='0.00')
    configurar_oracle_fake(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        financeiro.conciliar_faturamento(
            payload=ConciliacaoFaturamentoCreate(
                **payload_conciliacao(
                    remessas=[
                        {
                            'cd_remessa': 987,
                            'sn_glosado': True,
                            'valor_glosado': '121.00',
                        }
                    ]
                )
            ),
            usuario_atual=usuario_teste,
            session_postgres=session,
            session_oracle=object(),
        )

    assert exc_info.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert 'nao pode ser maior' in exc_info.value.detail


def test_marcacao_de_glosa_exige_valor_glosado_positivo():
    with pytest.raises(ValidationError) as exc_info:
        ConciliacaoFaturamentoCreate(
            **payload_conciliacao(
                remessas=[
                    {
                        'cd_remessa': CD_REMESSA_TESTE,
                        'sn_glosado': True,
                        'valor_glosado': '0.00',
                    }
                ]
            )
        )

    assert 'Informe um valor de glosa maior que zero' in str(exc_info.value)


def test_data_recebimento_exige_conta_bancaria():
    with pytest.raises(ValidationError) as exc_info:
        ConciliacaoFaturamentoCreate(
            **payload_conciliacao(data_recebimento='2026-08-10')
        )

    assert 'Selecione a conta bancaria' in str(exc_info.value)


def test_contas_bancarias_sao_mapeadas_da_view_hpc():
    class ResultadoContas:
        @staticmethod
        def all():
            return [
                SimpleNamespace(
                    cd_con_cor=7,
                    ds_con_cor='Banco Teste',
                    cd_agencia='1234',
                    cd_digito_agencia='5',
                    nr_conta='98765',
                    cd_digito_conta_corrente='4',
                )
            ]

    class OracleSession:
        @staticmethod
        def scalars(_query):
            return ResultadoContas()

    response = financeiro.consultar_contas_bancarias(
        usuario_atual=None,
        session_oracle=OracleSession(),
    )

    assert response == {
        'contas': [
            {
                'id': 7,
                'banco': 'Banco Teste',
                'descricao': 'Banco Teste',
                'agencia': '1234',
                'digito_agencia': '5',
                'conta': '98765',
                'digito': '4',
            }
        ]
    }


def _capturar_query_remessas(q):
    captured = {}

    class ResultadoVazio:
        @staticmethod
        def all():
            return []

    class OracleSession:
        @staticmethod
        def execute(query):
            captured['query'] = query
            return ResultadoVazio()

    financeiro._consultar_remessas_hpc(
        OracleSession(),
        '39427632000171',
        set(),
        q=q,
    )
    return str(captured['query'].compile(dialect=oracle.dialect()))


def test_pesquisa_numerica_de_remessa_e_exata():
    sql = _capturar_query_remessas('8495')

    assert 'cd_remessa =' in sql
    assert 'LIKE' not in sql


def test_pesquisa_textual_compila_cast_valido_para_oracle():
    sql = _capturar_query_remessas('SAUDE')

    assert 'VARCHAR2(50' in sql


def test_exclusao_de_remessas_respeita_limite_de_lista_do_oracle():
    captured = {}

    class ResultadoVazio:
        @staticmethod
        def all():
            return []

    class OracleSession:
        @staticmethod
        def execute(query):
            captured['query'] = query
            return ResultadoVazio()

    financeiro._consultar_remessas_hpc(
        OracleSession(),
        '39427632000171',
        set(range(1, 1802)),
    )
    sql = str(captured['query'].compile(dialect=oracle.dialect()))
    expected_chunks = 3

    assert sql.count('NOT IN') == expected_chunks


def test_informa_quando_remessa_foi_integralmente_conciliada(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session)
    configurar_oracle_fake(monkeypatch)
    monkeypatch.setattr(
        financeiro, '_remessas_conciliadas', lambda _session: {8495}
    )
    monkeypatch.setattr(
        financeiro,
        '_remessas_previamente_conciliadas',
        lambda _session: {8495},
    )

    response = financeiro.consultar_remessas_para_nfse(
        nfse_row_hash='nfse-1',
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
        q='8495',
        limit=50,
    )

    assert response['remessas'] == []
    assert response['message'] == (
        'A remessa 8495 foi integralmente recebida e conciliada.'
    )
    assert response['restricao'] == {
        'cd_remessa': 8495,
        'motivo': 'recebida_integralmente',
        'message': response['message'],
        'valor_total_acatado': Decimal('0.00'),
        'saldo_cobravel': Decimal('0.00'),
        'remessa_recebida_integralmente': True,
        'remessa_encerrada_financeiramente': True,
    }


def test_informa_que_remessa_com_glosa_precisa_de_recurso(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session)
    criar_conciliacao_anterior_com_glosa(session, usuario_teste.id)
    configurar_oracle_fake(monkeypatch)

    response = financeiro.consultar_remessas_para_nfse(
        nfse_row_hash='nfse-1',
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
        q=str(CD_REMESSA_TESTE),
        limit=50,
    )

    assert response['remessas'] == []
    assert response['message'] == (
        f'A remessa {CD_REMESSA_TESTE} já possui conciliação anterior e não '
        'possui recurso disponível para uma nova conciliação.'
    )
    assert response['restricao']['motivo'] == 'conciliacao_sem_recurso'
    assert response['restricao']['saldo_cobravel'] == Decimal('20.00')
    assert response['restricao']['remessa_encerrada_financeiramente'] is False


def test_remessa_conciliada_com_glosa_retorna_valor_do_recurso(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='20.00')
    criar_conciliacao_anterior_com_glosa(session, usuario_teste.id)
    criar_recurso_aberto(session)
    configurar_oracle_fake(monkeypatch)

    response = financeiro.consultar_remessas_para_nfse(
        nfse_row_hash='nfse-1',
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
        q=str(CD_REMESSA_TESTE),
        limit=50,
    )

    remessa = response['remessas'][0]
    assert remessa['tp_conciliacao'] == 'recurso'
    assert remessa['valor_remessa_original'] == Decimal('120.00')
    assert remessa['valor_total'] == Decimal('20.00')
    assert remessa['valor_recursado'] == Decimal('20.00')
    assert remessa['valor_total_acatado'] == Decimal('0.00')
    assert remessa['saldo_cobravel'] == Decimal('20.00')
    assert remessa['valor_elegivel_conciliacao'] == Decimal('20.00')
    assert remessa['situacao_financeira'] == 'recurso_aberto'


def test_recurso_libera_nova_conciliacao_sem_recebimento_anterior(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(
        session,
        row_hash='nfse-anterior',
        valor='100.00',
        numero_nfse='NFSE-ANTERIOR',
    )
    configurar_oracle_fake(monkeypatch)
    financeiro.conciliar_faturamento(
        payload=ConciliacaoFaturamentoCreate(
            **payload_conciliacao(row_hash='nfse-anterior')
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )
    assert session.scalar(select(RecebimentoRemessa)) is None
    criar_recurso_aberto(session)
    criar_nfse(
        session,
        row_hash='nfse-1',
        valor='20.00',
        numero_nfse='12345',
    )

    response = financeiro.consultar_remessas_para_nfse(
        nfse_row_hash='nfse-1',
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
        q=str(CD_REMESSA_TESTE),
        limit=50,
    )

    remessa = response['remessas'][0]
    assert remessa['tp_conciliacao'] == 'recurso'
    assert remessa['valor_remessa_original'] == Decimal('120.00')
    assert remessa['valor_recursado'] == Decimal('20.00')
    assert remessa['valor_recebimento_pendente'] == Decimal('20.00')
    assert remessa['saldo_cobravel'] == Decimal('120.00')
    assert remessa['valor_elegivel_conciliacao'] == Decimal('20.00')

    nova_conciliacao = financeiro.conciliar_faturamento(
        payload=ConciliacaoFaturamentoCreate(
            **payload_conciliacao(
                remessas=[
                    {
                        'cd_remessa': CD_REMESSA_TESTE,
                        'sn_glosado': False,
                        'valor_glosado': '0.00',
                    }
                ]
            )
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )
    assert nova_conciliacao['total_remessas'] == Decimal('20.00')


def test_conciliacao_anterior_sem_recurso_nao_duplica_remessa(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(
        session,
        row_hash='nfse-anterior',
        valor='120.00',
        numero_nfse='NFSE-ANTERIOR',
    )
    configurar_oracle_fake(monkeypatch)
    financeiro.conciliar_faturamento(
        payload=ConciliacaoFaturamentoCreate(
            **payload_conciliacao(
                row_hash='nfse-anterior',
                remessas=[
                    {
                        'cd_remessa': CD_REMESSA_TESTE,
                        'sn_glosado': False,
                        'valor_glosado': '0.00',
                    }
                ],
            )
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )
    assert session.scalar(select(RecebimentoRemessa)) is None
    criar_nfse(
        session,
        row_hash='nfse-1',
        valor='120.00',
        numero_nfse='12345',
    )

    response = financeiro.consultar_remessas_para_nfse(
        nfse_row_hash='nfse-1',
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
        q=str(CD_REMESSA_TESTE),
        limit=50,
    )

    assert response['remessas'] == []
    assert 'já possui conciliação anterior' in response['message']
    assert 'não possui recurso disponível' in response['message']


@pytest.mark.parametrize('valor_recurso', ['10.00', '15.00'])
def test_recurso_independente_do_saldo_financeiro_libera_conciliacao(
    session,
    usuario_teste,
    monkeypatch,
    valor_recurso,
):
    criar_nfse(session, valor=valor_recurso)
    criar_conciliacao_anterior_com_glosa(session, usuario_teste.id)
    criar_recurso_aberto(session, valor_recursado=valor_recurso)
    configurar_oracle_fake(monkeypatch)

    response = financeiro.consultar_remessas_para_nfse(
        nfse_row_hash='nfse-1',
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
        q=str(CD_REMESSA_TESTE),
        limit=50,
    )

    remessa = response['remessas'][0]
    assert remessa['valor_recursado'] == Decimal(valor_recurso)
    assert remessa['valor_recebimento_pendente'] == Decimal(valor_recurso)
    assert remessa['saldo_cobravel'] == Decimal('20.00')
    assert remessa['valor_elegivel_conciliacao'] == Decimal(valor_recurso)

    conciliacao = financeiro.conciliar_faturamento(
        payload=ConciliacaoFaturamentoCreate(
            **payload_conciliacao(
                remessas=[
                    {
                        'cd_remessa': CD_REMESSA_TESTE,
                        'sn_glosado': False,
                        'valor_glosado': '0.00',
                    }
                ]
            )
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )

    assert conciliacao['total_remessas'] == Decimal(valor_recurso)


def test_concilia_recurso_usando_valor_recursado_como_total_da_remessa(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='20.00')
    criar_conciliacao_anterior_com_glosa(session, usuario_teste.id)
    criar_recurso_aberto(session)
    configurar_oracle_fake(monkeypatch)

    response = financeiro.conciliar_faturamento(
        payload=ConciliacaoFaturamentoCreate(
            **payload_conciliacao(
                remessas=[
                    {
                        'cd_remessa': CD_REMESSA_TESTE,
                        'sn_glosado': False,
                        'valor_glosado': '0.00',
                    }
                ]
            )
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )

    assert response['total_remessas'] == Decimal('20.00')
    assert response['total_glosas'] == Decimal('0.00')
    remessas = session.scalars(
        select(ConciliacaoFaturamentoRemessa).order_by(
            ConciliacaoFaturamentoRemessa.id
        )
    ).all()
    assert [remessa.tp_conciliacao for remessa in remessas] == [
        'faturamento',
        'recurso',
    ]
    assert remessas[-1].valor_total == Decimal('20.00')
    assert remessas[-1].sn_glosado == 'not'


def test_recurso_pode_ter_nova_glosa_e_exige_recurso_adicional(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='15.00')
    criar_conciliacao_anterior_com_glosa(session, usuario_teste.id)
    criar_recurso_aberto(session)
    configurar_oracle_fake(monkeypatch)

    response = financeiro.conciliar_faturamento(
        payload=ConciliacaoFaturamentoCreate(
            **payload_conciliacao(
                remessas=[
                    {
                        'cd_remessa': CD_REMESSA_TESTE,
                        'sn_glosado': True,
                        'valor_glosado': '5.00',
                    }
                ]
            )
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )

    assert response['total_remessas'] == Decimal('20.00')
    assert response['total_glosas'] == Decimal('5.00')
    remessas = session.scalars(
        select(ConciliacaoFaturamentoRemessa).order_by(
            ConciliacaoFaturamentoRemessa.id
        )
    ).all()
    assert remessas[-1].tp_conciliacao == 'recurso'
    assert remessas[-1].sn_glosado == 'true'
    assert remessas[-1].valor_glosado == Decimal('5.00')
    assert financeiro._recursos_abertos_por_remessa(
        session,
        {CD_REMESSA_TESTE},
    ) == {}

    criar_recurso_aberto(
        session,
        processo_recurso='REC-ADICIONAL',
    )

    assert financeiro._recursos_abertos_por_remessa(
        session,
        {CD_REMESSA_TESTE},
    ) == {CD_REMESSA_TESTE: Decimal('20.00')}


def test_retorna_remessa_com_recurso_aberto_e_valor_recursado(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session)
    criar_recurso_aberto(session)
    configurar_oracle_fake(monkeypatch)
    monkeypatch.setattr(
        financeiro, '_remessas_conciliadas', lambda _session: set()
    )

    response = financeiro.consultar_remessas_para_nfse(
        nfse_row_hash='nfse-1',
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
        q='987',
        limit=50,
    )

    assert response['remessas'][0]['cd_remessa'] == CD_REMESSA_TESTE
    assert response['remessas'][0]['possui_recurso_aberto'] is True
    assert response['remessas'][0]['valor_recursado'] == Decimal('20.00')
    assert response['remessas'][0]['valor_total'] == Decimal('20.00')
    assert response['remessas'][0]['tp_conciliacao'] == 'recurso'


def test_conciliacao_usa_valor_recursado_do_banco(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='20.00')
    criar_recurso_aberto(session)
    configurar_oracle_fake(monkeypatch)

    response = financeiro.conciliar_faturamento(
        payload=ConciliacaoFaturamentoCreate(
            **payload_conciliacao(
                remessas=[
                    {
                        'cd_remessa': 987,
                        'sn_glosado': False,
                        'valor_glosado': '0.00',
                    }
                ]
            )
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )

    assert response['total_remessas'] == Decimal('20.00')
    assert response['total_glosas'] == Decimal('0.00')
    remessa = session.scalar(select(ConciliacaoFaturamentoRemessa))
    assert remessa.tp_conciliacao == 'recurso'
    assert remessa.sn_glosado == 'not'
    assert remessa.valor_glosado == Decimal('0.00')


def test_recurso_recebido_nao_e_considerado_em_aberto(session):
    criar_recurso_aberto(
        session,
        dt_recebimento=date(2026, 7, 1),
        valor_recebido=Decimal('20.00'),
        qtd_recebida=Decimal('1.00'),
    )

    assert financeiro._recursos_abertos_por_remessa(
        session,
        {CD_REMESSA_TESTE},
    ) == {}


def test_recurso_parcialmente_pago_nao_e_recurso_sem_pagamento(session):
    criar_recurso_aberto(
        session,
        dt_recebimento=date(2026, 7, 1),
        valor_recebido=Decimal('7.50'),
        qtd_recebida=Decimal('1.00'),
    )

    assert financeiro._recursos_abertos_por_remessa(
        session,
        {CD_REMESSA_TESTE},
    ) == {}


def test_recurso_parcialmente_pago_nao_libera_nova_nfse(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='12.50')
    criar_conciliacao_anterior_com_glosa(session, usuario_teste.id)
    criar_recurso_aberto(
        session,
        dt_recebimento=date(2026, 7, 1),
        valor_recebido=Decimal('7.50'),
        qtd_recebida=Decimal('1.00'),
    )
    configurar_oracle_fake(monkeypatch)

    response = financeiro.consultar_remessas_para_nfse(
        nfse_row_hash='nfse-1',
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
        q=str(CD_REMESSA_TESTE),
        limit=50,
    )

    assert response['remessas'] == []
    assert 'não possui recurso disponível' in response['message']

    with pytest.raises(HTTPException) as exc_info:
        financeiro.conciliar_faturamento(
            payload=ConciliacaoFaturamentoCreate(
                **payload_conciliacao(
                    remessas=[
                        {
                            'cd_remessa': CD_REMESSA_TESTE,
                            'sn_glosado': False,
                            'valor_glosado': '0.00',
                        }
                    ]
                )
            ),
            usuario_atual=usuario_teste,
            session_postgres=session,
            session_oracle=object(),
        )

    assert exc_info.value.status_code == HTTPStatus.CONFLICT
    assert 'não possui recurso disponível' in exc_info.value.detail


def test_acato_integral_encerra_saldo_sem_marcar_recebimento_integral(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='20.00')
    criar_conciliacao_anterior_com_glosa(session, usuario_teste.id)
    criar_recurso_aberto(
        session,
        sn_glosado='not',
        processo_recurso='ACATO-20',
    )
    configurar_oracle_fake(monkeypatch)

    remessa_financeira = session.get(
        RemessaFinanceira,
        CD_REMESSA_TESTE,
    )
    assert remessa_financeira.recebimento_integral is False
    assert financeiro._valores_acatados_por_remessa(
        session,
        {CD_REMESSA_TESTE},
    ) == {CD_REMESSA_TESTE: Decimal('20.00')}
    assert financeiro._saldos_recebimento_por_remessa(
        session,
        {CD_REMESSA_TESTE},
    ) == {}

    response = financeiro.consultar_remessas_para_nfse(
        nfse_row_hash='nfse-1',
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
        q=str(CD_REMESSA_TESTE),
        limit=50,
    )

    assert response['remessas'] == []
    assert 'saldo remanescente foi integralmente acatado' in response[
        'message'
    ]
    assert response['restricao']['motivo'] == 'encerrada_por_acato'
    assert response['restricao']['valor_total_acatado'] == Decimal('20.00')
    assert response['restricao']['saldo_cobravel'] == Decimal('0.00')
    assert response['restricao']['remessa_recebida_integralmente'] is False
    assert response['restricao']['remessa_encerrada_financeiramente'] is True

    with pytest.raises(HTTPException) as exc_info:
        financeiro.registrar_recebimento_remessa(
            payload=RecebimentoRemessaCreate(
                cd_remessa=CD_REMESSA_TESTE,
                numero_nfse='NFSE-ANTERIOR',
                data_recebimento='2026-07-10',
                valor_recebido='1.00',
                conta_bancaria_id=CONTA_BANCARIA_TESTE,
            ),
            usuario_atual=usuario_teste,
            session_postgres=session,
            session_oracle=OracleComContaFake(),
        )

    assert exc_info.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert 'deve ser exatamente R$ 100,00' in exc_info.value.detail


def test_acato_parcial_reduz_saldo_e_recurso_quita_parte_recursada(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='15.00')
    criar_conciliacao_anterior_com_glosa(session, usuario_teste.id)
    criar_recurso_aberto(
        session,
        valor_recursado='5.00',
        sn_glosado='not',
        processo_recurso='ACATO-5',
    )
    criar_recurso_aberto(
        session,
        valor_recursado='15.00',
        processo_recurso='RECURSO-15',
    )
    configurar_oracle_fake(monkeypatch)

    response = financeiro.consultar_remessas_para_nfse(
        nfse_row_hash='nfse-1',
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
        q=str(CD_REMESSA_TESTE),
        limit=50,
    )

    remessa = response['remessas'][0]
    assert remessa['valor_remessa_original'] == Decimal('120.00')
    assert remessa['valor_recursado'] == Decimal('15.00')
    assert remessa['valor_recebimento_pendente'] == Decimal('15.00')
    assert remessa['valor_total_acatado'] == Decimal('5.00')
    assert remessa['saldo_cobravel'] == Decimal('15.00')
    assert remessa['valor_elegivel_conciliacao'] == Decimal('15.00')
    assert (
        remessa['situacao_financeira']
        == 'recurso_aberto_com_acato_parcial'
    )

    financeiro.conciliar_faturamento(
        payload=ConciliacaoFaturamentoCreate(
            **payload_conciliacao(
                data_recebimento='2026-07-10',
                conta_bancaria_id=CONTA_BANCARIA_TESTE,
                remessas=[
                    {
                        'cd_remessa': CD_REMESSA_TESTE,
                        'sn_glosado': False,
                        'valor_glosado': '0.00',
                    }
                ],
            )
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=OracleComContaFake(),
    )

    remessa_financeira = session.get(
        RemessaFinanceira,
        CD_REMESSA_TESTE,
    )
    assert remessa_financeira.recebimento_integral is False
    assert financeiro._saldos_recebimento_por_remessa(
        session,
        {CD_REMESSA_TESTE},
    ) == {}
    recebimentos = financeiro.consultar_recebimentos_remessas(
        usuario_atual=usuario_teste,
        session=session,
        cd_remessa=CD_REMESSA_TESTE,
        numero_nfse=None,
        limit=100,
        offset=0,
    )['recebimentos']
    assert recebimentos[0]['valor_total_recebido'] == Decimal('115.00')
    assert recebimentos[0]['valor_total_acatado'] == Decimal('5.00')
    assert recebimentos[0]['saldo_em_aberto'] == Decimal('0.00')
    assert recebimentos[0]['remessa_recebida_integralmente'] is False
    assert recebimentos[0]['remessa_encerrada_financeiramente'] is True


@pytest.mark.parametrize(
    'overrides',
    [
        {'processo_recurso': None},
        {'dt_recurso': None},
        {'sn_glosado': 'not'},
        {'sn_ativo': 'not'},
    ],
)
def test_registro_sem_recurso_ativo_nao_e_considerado_em_aberto(
    session,
    overrides,
):
    criar_recurso_aberto(session, **overrides)

    assert financeiro._recursos_abertos_por_remessa(
        session,
        {CD_REMESSA_TESTE},
    ) == {}


def test_recebimentos_por_remessa_quitam_em_nfses_distintas(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session)
    configurar_oracle_fake(monkeypatch)

    financeiro.conciliar_faturamento(
        payload=ConciliacaoFaturamentoCreate(
            **payload_conciliacao(
                data_recebimento='2026-07-10',
                conta_bancaria_id=7,
            )
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=OracleComContaFake(),
    )

    remessa = session.get(RemessaFinanceira, CD_REMESSA_TESTE)
    recebimentos = session.scalars(
        select(RecebimentoRemessa).order_by(RecebimentoRemessa.id)
    ).all()
    assert remessa.valor_total == Decimal('120.00')
    assert remessa.recebimento_integral is False
    assert len(recebimentos) == 1
    assert recebimentos[0].numero_nfse == '12345'
    assert recebimentos[0].valor_recebido == Decimal('100.00')
    assert recebimentos[0].usuario_id == usuario_teste.id
    assert recebimentos[0].conta_bancaria_id == CONTA_BANCARIA_TESTE
    assert recebimentos[0].recebimento_integral is False

    criar_nfse(
        session,
        row_hash='nfse-2',
        valor='20.00',
        numero_nfse='67890',
    )
    criar_recurso_aberto(session)
    financeiro.conciliar_faturamento(
        payload=ConciliacaoFaturamentoCreate(
            **payload_conciliacao(
                row_hash='nfse-2',
                data_recebimento='2026-07-11',
                conta_bancaria_id=7,
                remessas=[
                    {
                        'cd_remessa': CD_REMESSA_TESTE,
                        'sn_glosado': False,
                        'valor_glosado': '0.00',
                    }
                ],
            )
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=OracleComContaFake(),
    )

    session.refresh(remessa)
    recebimentos = session.scalars(
        select(RecebimentoRemessa).order_by(RecebimentoRemessa.id)
    ).all()
    assert remessa.recebimento_integral is True
    assert [item.numero_nfse for item in recebimentos] == ['12345', '67890']
    assert [item.valor_recebido for item in recebimentos] == [
        Decimal('100.00'),
        Decimal('20.00'),
    ]
    assert [item.recebimento_integral for item in recebimentos] == [
        False,
        True,
    ]

    response = financeiro.consultar_recebimentos_remessas(
        usuario_atual=usuario_teste,
        session=session,
        cd_remessa=CD_REMESSA_TESTE,
        numero_nfse=None,
        limit=100,
        offset=0,
    )
    assert response['total'] == len(recebimentos)
    assert response['recebimentos'][0]['valor_total_recebido'] == Decimal(
        '120.00'
    )
    assert response['recebimentos'][0]['saldo_em_aberto'] == Decimal('0.00')
    assert response['recebimentos'][0][
        'remessa_recebida_integralmente'
    ] is True


def test_recebimento_posterior_exige_valor_exato(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='120.00')
    configurar_oracle_fake(monkeypatch)
    financeiro.conciliar_faturamento(
        payload=ConciliacaoFaturamentoCreate(
            **payload_conciliacao(
                remessas=[
                    {
                        'cd_remessa': CD_REMESSA_TESTE,
                        'sn_glosado': False,
                        'valor_glosado': '0.00',
                    }
                ]
            )
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )

    lancamento = LancamentoExtratoBancario(
        conta_bancaria_id=CONTA_BANCARIA_TESTE,
        data_lancamento=date(2026, 7, 10),
        valor=Decimal('120.00'),
        descricao='Recebimento NFS-e 12345',
    )
    lancamento.data_criacao = datetime(2026, 7, 10, 9, 0)
    session.add(lancamento)
    session.commit()
    payload_recebimento = RecebimentoRemessaCreate(
        cd_remessa=CD_REMESSA_TESTE,
        numero_nfse='12345',
        data_recebimento='2026-07-10',
        valor_recebido='50.00',
        conta_bancaria_id=7,
        conta_plano_contas='1.1.1',
        conta_centro_custo='CC-10',
        lancamento_extrato_id=lancamento.id,
    )
    with pytest.raises(HTTPException) as exc_info:
        financeiro.registrar_recebimento_remessa(
            payload=payload_recebimento,
            usuario_atual=usuario_teste,
            session_postgres=session,
            session_oracle=OracleComContaFake(),
        )
    assert exc_info.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert 'exatamente R$ 120,00' in exc_info.value.detail
    assert session.scalar(select(RecebimentoRemessa)) is None
    assert (
        session.get(LancamentoExtratoBancario, lancamento.id).conciliado
        is False
    )

    payload_exato = payload_recebimento.model_copy(
        update={'valor_recebido': Decimal('120.00')}
    )
    response = financeiro.registrar_recebimento_remessa(
        payload=payload_exato,
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=OracleComContaFake(),
    )
    assert response['recebimento_integral'] is True
    assert response['remessa_recebida_integralmente'] is True
    assert response['valor_total_recebido'] == Decimal('120.00')
    assert response['saldo_em_aberto'] == Decimal('0.00')
    recebimento = session.scalar(select(RecebimentoRemessa))
    assert recebimento.conta_plano_contas == '1.1.1'
    assert recebimento.conta_centro_custo == 'CC-10'
    assert recebimento.lancamento_extrato_id == lancamento.id
    assert (
        session.get(LancamentoExtratoBancario, lancamento.id).conciliado
        is True
    )
    with pytest.raises(HTTPException) as duplicate_info:
        financeiro.registrar_recebimento_remessa(
            payload=payload_exato.model_copy(
                update={'lancamento_extrato_id': None}
            ),
            usuario_atual=usuario_teste,
            session_postgres=session,
            session_oracle=OracleComContaFake(),
        )
    assert duplicate_info.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert 'excede o saldo em aberto' in duplicate_info.value.detail
