import re
from datetime import date, datetime
from decimal import Decimal
from http import HTTPStatus
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import insert, select
from sqlalchemy.dialects import oracle
from sqlalchemy.exc import SQLAlchemyError

from app_prontocardio.models import (
    AssociacaoItemIpmManual,
    AuditoriaConciliacaoFaturamento,
    ConciliacaoFaturamento,
    ConciliacaoFaturamentoRemessa,
    LancamentoExtratoBancario,
    NfseXml,
    ProcessoConciliacaoRemessa,
    RecebimentoRemessa,
    RegistroGlosa,
    RemessaFinanceira,
    TipoAtendimento,
)
from app_prontocardio.routers import app_glosas, financeiro
from app_prontocardio.schema import (
    AssociacaoItemIpmManualCreate,
    AssociacaoItemIpmManualUpdate,
    CardFollowUpGlosaPublic,
    ConciliacaoFaturamentoCreate,
    ConciliacaoFaturamentoUpdate,
    ConciliacaoRemessaCreate,
    ConciliacaoRemessaPublic,
    ConciliacoesGerenciamentoList,
    ConciliacoesSemRecebimentoList,
    RecebimentoRemessaCreate,
    RecebimentoRemessaUpdate,
    RegistroGlosaCreate,
)

CD_REMESSA_TESTE = 987
CONTA_BANCARIA_TESTE = 7
ITENS_ANALITICOS_TESTE = 2
CONCILIACOES_DISTRIBUIDAS = 2
PARCELAS_ANTES_QUITACAO = 2
GRU_PRO_DIAGNOSTICO = 10
GRU_PRO_MEDICAMENTOS = 20
GRU_FAT_EXAMES = 1
GRU_FAT_MEDICAMENTOS = 2
REMESSA_RELATORIO_TRAMITANDO = 19218
CONTA_RELATORIO_TRAMITANDO = 123456
ATENDIMENTO_RELATORIO_TRAMITANDO = 314159
LANCAMENTO_RELATORIO_TRAMITANDO = 101
REMESSA_ASSOCIACAO_MANUAL = 16425
REMESSA_ASSOCIACAO_MANUAL_EDITADA = 16426
CONTA_ASSOCIACAO_MANUAL = 343332
CONTA_ASSOCIACAO_MANUAL_EDITADA = 343333
LANCAMENTO_ASSOCIACAO_MANUAL = 9
LANCAMENTO_ASSOCIACAO_MANUAL_EDITADO = 10
ID_ASSOCIACAO_MANUAL = 7


def test_schema_do_card_preserva_indicacao_de_recurso():
    card = CardFollowUpGlosaPublic.model_validate(
        {
            'cd_remessa': 17923,
            'convenio': 'IPM',
            'numero_nfse': '',
            'valor_remessa': '112.38',
            'valor_itens': '112.38',
            'valor_glosado': '106.38',
            'valor_glosa_pendente': '21.38',
            'valor_total_tratado': '85.00',
            'possui_recurso': True,
            'processo': {'numero_processo': 'P197016/2026'},
            'fiscal': {
                'numero_nfse': '',
                'valor_servicos': '0',
                'impostos': '0',
                'valor_liquido_nfse': '0',
            },
            'pacientes': [],
        }
    )

    assert card.possui_recurso is True


def test_protocolo_cogestao_completa_card_sem_demonstrativo(monkeypatch):
    class ResultadoFake:
        def mappings(self):
            return [
                {
                    'processo': 'p058752/2026',
                    'valor_glosado': Decimal('70.00'),
                    'numero_protocolo': '4124037',
                }
            ]

    class SessaoFake:
        def execute(self, _consulta, _parametros=None):
            return ResultadoFake()

    monkeypatch.setattr(
        financeiro,
        '_tabela_ipm_existe',
        lambda *_args: True,
    )

    protocolos = (
        financeiro._protocolos_cogestao_por_processo_glosa_follow_up(
            SessaoFake(),
            {'p058752/2026'},
        )
    )

    assert protocolos == {
        ('p058752/2026', Decimal('70.00')): '4124037'
    }


def test_recursos_processos_filtra_periodo_e_calcula_cards(monkeypatch):
    card = {
        'cd_remessa': 17923,
        'data_competencia': date(2026, 7, 1),
        'processo': {'numero_processo': 'P197016/2026'},
    }
    monkeypatch.setattr(
        financeiro,
        'consultar_follow_up_glosas',
        lambda **_kwargs: {'cards': [card], 'total': 1},
    )

    class SessionSemCadastros:
        @staticmethod
        def scalars(_consulta):
            return []

    resultado = financeiro.consultar_processos_recurso(
        usuario_atual=SimpleNamespace(id=1),
        session=SessionSemCadastros(),
        session_oracle=SimpleNamespace(),
        processo_original=None,
        processo_recurso=None,
        paciente=None,
        periodo='07/2026',
        situacao=None,
        detalhar_processo=None,
        incluir_detalhes=False,
        limit=10,
        offset=0,
    )

    assert resultado['total'] == 1
    assert resultado['quantidade_com_processo_recurso'] == 0
    assert resultado['quantidade_sem_processo_recurso'] == 1
    assert resultado['processos'][0]['processo_original'] == 'P197016/2026'


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
            valor_inss='6.00',
            valor_iss_retido='7.00',
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
            'data_competencia': date(2026, 7, 1),
            'valor_total': '120.00',
        }
    ]


def test_normaliza_chave_da_associacao_manual_por_processo_competencia_e_nr():
    assert financeiro._normalizar_chave_associacao_manual(
        ' p123/2026 ', '05/2026', ' nr-10 '
    ) == ('P123/2026', '05/2026', 'NR-10')


def test_seleciona_remessa_manual_da_cogestao_sem_depender_do_valor():
    remessa_manual = {
        'cd_remessa': 17058,
        'valor_total': Decimal('5957.56'),
        'data_competencia': date(2026, 2, 1),
    }

    selecionada = financeiro._selecionar_remessa_cogestao(
        {
            'cd_remessa_manual': 17058,
            'valor_protocolo': Decimal('5957.56'),
            'competencia_producao': '02/2026',
        },
        {},
        {17058: remessa_manual},
    )

    assert selecionada == remessa_manual


def test_seleciona_remessa_indicada_pelo_spu_com_competencia_diferente():
    remessa_spu = {
        'cd_remessa': 16425,
        'valor_total': Decimal('5846.55'),
        'data_competencia': date(2026, 1, 1),
    }

    selecionada = financeiro._selecionar_remessa_cogestao(
        {
            'numero_processo': 'P058752/2026',
            'nr': '4124085',
            'cd_remessa_manual': None,
            'valor_protocolo': Decimal('5846.55'),
            'competencia_producao': '12/2025',
        },
        {},
        {},
        {('p058752/2026', '4124085'): remessa_spu},
    )

    assert selecionada == remessa_spu


def test_complemento_cogestao_preserva_snapshot_quando_oracle_oscila(
    monkeypatch,
):
    def oracle_indisponivel(*_args):
        raise SQLAlchemyError('Oracle indisponível')

    persistencias = []
    monkeypatch.setattr(
        financeiro,
        '_remessas_cogestao_oracle',
        oracle_indisponivel,
    )
    monkeypatch.setattr(
        financeiro,
        '_persistir_remessas_cogestao',
        lambda *_args: persistencias.append(True),
    )

    resultado = financeiro._complementar_remessas_cogestao(
        SimpleNamespace(),
        SimpleNamespace(),
        {Decimal('100.00')},
        {'12345678000190'},
        {date(2026, 8, 1)},
    )

    assert resultado == {}
    assert persistencias == []


def test_verificacao_de_tabelas_reutiliza_catalogo_na_mesma_sessao(
    monkeypatch,
):
    class Inspector:
        calls = 0

        def get_table_names(self, *, schema):
            assert schema == 'api_prontocardio'
            self.calls += 1
            return ['processos_ipm', 'demonstrativo_conta_ipm']

    inspector = Inspector()
    class Session:
        info = {}

        @staticmethod
        def get_bind():
            return SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))

        @staticmethod
        def connection():
            return object()

    session = Session()
    monkeypatch.setattr(financeiro, 'inspect', lambda _connection: inspector)

    assert financeiro._tabela_ipm_existe(session, 'processos_ipm')
    assert financeiro._tabela_ipm_existe(session, 'demonstrativo_conta_ipm')
    assert not financeiro._tabela_ipm_existe(session, 'tabela_ausente')
    assert inspector.calls == 1


def test_resumo_da_cogestao_identifica_valor_e_recurso_ativo():
    tratativas = {
        ('p094380/2026', 16839, 1, 2, 3): [
            SimpleNamespace(
                sn_ativo='true',
                status_tratativa='recurso',
                valor_recursado=Decimal('188.12'),
            )
        ],
        ('p094380/2026', 16839, 4, 5, 6): [
            SimpleNamespace(
                sn_ativo='false',
                status_tratativa='recurso',
                valor_recursado=Decimal('999.00'),
            ),
            SimpleNamespace(
                sn_ativo='true',
                status_tratativa='pendente',
                valor_recursado=None,
            ),
        ],
    }

    valor, possui_recurso = (
        financeiro._resumo_tratativas_cogestao_remessa(
            tratativas,
            ' P094380/2026 ',
            16839,
        )
    )

    assert valor == Decimal('188.12')
    assert possui_recurso is True


def test_resumo_da_cogestao_nao_confunde_acato_com_recurso():
    tratativas = {
        ('p094380/2026', 17055, 1, 2, 3): [
            SimpleNamespace(
                sn_ativo='true',
                status_tratativa='acato',
                valor_recursado=Decimal('564.36'),
            )
        ]
    }

    valor, possui_recurso = (
        financeiro._resumo_tratativas_cogestao_remessa(
            tratativas,
            'P094380/2026',
            17055,
        )
    )

    assert valor == Decimal('564.36')
    assert possui_recurso is False


def test_marca_recurso_no_card_resumido_sem_carregar_pacientes():
    class Resultado:
        @staticmethod
        def all():
            return [(' P094380/2026 ', 16839)]

    class Sessao:
        @staticmethod
        def execute(_query):
            return Resultado()

    cards = [
        {
            'cd_remessa': 16839,
            'possui_recurso': False,
            'processo': {'numero_processo': 'P094380/2026'},
            'pacientes': [],
        },
        {
            'cd_remessa': 17055,
            'possui_recurso': False,
            'processo': {'numero_processo': 'P094380/2026'},
            'pacientes': [],
        },
    ]

    financeiro._marcar_cards_com_recurso_ativo(Sessao(), cards)

    assert cards[0]['possui_recurso'] is True
    assert cards[1]['possui_recurso'] is False


def cards_remessas_hpc(*_args, **kwargs):
    codigo = int(kwargs.get('q') or CD_REMESSA_TESTE)
    return (
        [
            {
                'cd_remessa': codigo,
                'cd_convenio': 10,
                'convenio': 'Convenio Teste',
                'cnpj_convenio': '98765432000110',
                'data_competencia': date(2026, 7, 1),
                'valor_total': Decimal('120.00'),
            }
        ],
        1,
    )


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
            'cd_gru_pro': GRU_PRO_DIAGNOSTICO,
            'ds_gru_pro': 'Diagnostico',
            'cd_gru_fat': GRU_FAT_EXAMES,
            'ds_gru_fat': 'EXAMES E DIAGNOSTICOS',
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
            'cd_gru_pro': GRU_PRO_MEDICAMENTOS,
            'ds_gru_pro': 'Medicamentos',
            'cd_gru_fat': GRU_FAT_MEDICAMENTOS,
            'ds_gru_fat': 'MEDICAMENTOS',
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
        'motivo_glosa': '1714',
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
    values.setdefault(
        'origem_registro',
        'conciliacao'
        if values.get('conciliacao_remessa_id') is not None
        else 'triagem',
    )
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


def configurar_cards_oracle_fake(monkeypatch):
    monkeypatch.setattr(
        financeiro,
        '_consultar_cards_remessas_hpc',
        cards_remessas_hpc,
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
        motivo_glosa='1714',
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


def test_endpoint_gera_pdf_consolidado_por_processo(
    monkeypatch,
    usuario_teste,
):
    card = {
        'cd_remessa': 987,
        'processo': {'numero_processo': 'PROC-12/2026'},
        'pacientes': [],
    }
    monkeypatch.setattr(
        financeiro,
        'consultar_follow_up_glosas',
        lambda **_kwargs: {'cards': [card]},
    )
    monkeypatch.setattr(
        financeiro,
        'gerar_pdf_recurso_glosa',
        lambda cards_recebidos: (
            b'%PDF-1.7\nrecurso'
            if cards_recebidos == [card]
            else b''
        ),
    )

    response = financeiro.gerar_pdf_recurso_follow_up(
        usuario_atual=usuario_teste,
        session=object(),
        session_oracle=object(),
        processo_original='PROC-12/2026',
        download=False,
    )

    assert response.media_type == 'application/pdf'
    assert response.body == b'%PDF-1.7\nrecurso'
    assert response.headers['content-disposition'] == (
        'inline; filename="recurso-glosa-PROC-12-2026.pdf"'
    )


def test_distribui_recurso_agregado_entre_linhas_do_demonstrativo():
    registro = SimpleNamespace(
        id=1,
        sn_ativo='true',
        status_tratativa='recurso',
        valor_recursado=Decimal('520.14'),
    )
    itens = [
        ({'valor_glosa': Decimal('120.03')}, [registro]),
        ({'valor_glosa': Decimal('400.11')}, [registro]),
    ]

    financeiro._distribuir_tratativas_itens_demonstrativo(itens)

    assert [item['valor_total_tratado'] for item, _ in itens] == [
        Decimal('120.03'),
        Decimal('400.11'),
    ]
    assert [item['valor_pendente'] for item, _ in itens] == [
        Decimal('0.00'),
        Decimal('0.00'),
    ]
    assert all(item['registro_recusa'] is registro for item, _ in itens)


def test_distribui_recurso_parcial_sem_duplicar_valor_tratado():
    registro = SimpleNamespace(
        id=1,
        sn_ativo='true',
        status_tratativa='recurso',
        valor_recursado=Decimal('200.00'),
    )
    itens = [
        ({'valor_glosa': Decimal('120.03')}, [registro]),
        ({'valor_glosa': Decimal('400.11')}, [registro]),
    ]

    financeiro._distribuir_tratativas_itens_demonstrativo(itens)

    assert [item['valor_total_tratado'] for item, _ in itens] == [
        Decimal('46.15'),
        Decimal('153.85'),
    ]
    assert sum(
        item['valor_total_tratado'] for item, _ in itens
    ) == Decimal('200.00')


def test_tratativa_conciliada_participa_do_detalhamento_demonstrativo(
    session,
    usuario_teste,
):
    vinculo = criar_conciliacao_anterior_com_glosa(
        session,
        usuario_teste.id,
        cd_remessa=987,
    )
    registro = criar_recurso_aberto(
        session,
        cd_remessa=987,
        conciliacao_remessa_id=vinculo.id,
        processo_controle_fatura_gab='PROC-ANTERIOR',
        cd_atendimento=2,
        conta=3,
        cd_lancamento=7,
    )

    tratativas = financeiro._tratativas_demonstrativo_por_item(
        session,
        {987},
    )

    chave = ('proc-anterior', 987, 2, 3, 7)
    assert [item.id for item in tratativas[chave]] == [registro.id]


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
        'impostos': Decimal('28.00'),
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
    assert {
        registro.origem_registro for registro in registros_glosa
    } == {'conciliacao'}
    assert {
        registro.status_tratativa for registro in registros_glosa
    } == {'pendente'}
    assert sum(
        registro.valor_indicador for registro in registros_glosa
    ) == Decimal('20.00')
    assert sum(
        registro.valor_indicador > 0 for registro in registros_glosa
    ) == 1
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


def test_follow_up_exibe_glosas_registradas_inclusive_tratadas(  # noqa: PLR0915
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
        numero_nfse='1234',
        cd_remessa=CD_REMESSA_TESTE,
        convenio='Teste',
        limit=20,
        offset=0,
    )

    assert follow_up['total'] == 1
    assert follow_up['valor_total_glosado'] == Decimal('20.00')
    assert follow_up['valor_total_pendente'] == Decimal('20.00')
    assert follow_up['valor_total_tratado'] == Decimal('0.00')
    card = follow_up['cards'][0]
    assert card['cd_remessa'] == CD_REMESSA_TESTE
    assert card['numero_nfse'] == '12345'
    assert card['valor_remessa'] == Decimal('120.00')
    assert card['valor_glosado'] == Decimal('20.00')
    assert card['valor_total_tratado'] == Decimal('0.00')
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
    assert primeiro_item['cd_gru_pro'] == GRU_PRO_DIAGNOSTICO
    assert primeiro_item['ds_gru_pro'] == 'Diagnostico'
    assert primeiro_item['cd_gru_fat'] == GRU_FAT_EXAMES
    assert primeiro_item['ds_gru_fat'] == 'EXAMES E DIAGNOSTICOS'
    assert primeiro_item['dt_alta'] == datetime(2026, 6, 1, 12, 0)
    assert primeiro_item['dt_lancamento'] == datetime(2026, 6, 1, 8, 30)

    resumo = financeiro.consultar_follow_up_glosas(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        limit=20,
        offset=0,
        conciliacao_remessa_id=card['conciliacao_remessa_id'],
        incluir_detalhes=False,
    )
    assert resumo['total'] == 1
    assert resumo['cards'][0]['pacientes'] == []

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
    assert follow_up['valor_total_tratado'] == Decimal('10.00')
    assert follow_up['cards'][0]['valor_total_tratado'] == Decimal('10.00')

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
    assert len(follow_up['cards']) == 1
    assert follow_up['cards'][0]['valor_glosa_pendente'] == Decimal('0.00')

    response = app_glosas.deletar_glosa(
        registros_glosa[0].id,
        usuario_atual=usuario_teste,
        session=session,
    )
    session.refresh(registros_glosa[0])
    assert response == {'message': 'Registro de glosa desfeito!'}
    assert registros_glosa[0].sn_ativo == 'true'
    assert registros_glosa[0].status_tratativa == 'pendente'
    assert registros_glosa[0].processo_recurso is None
    assert registros_glosa[0].qtd_recursado is None
    assert registros_glosa[0].valor_recursado is None
    assert registros_glosa[0].dt_recurso is None
    assert registros_glosa[0].motivo_glosa == '1714'
    assert 'Pendente de tratativa da NFS-e 12345' in (
        registros_glosa[0].descricao_glosa
    )

    follow_up = financeiro.consultar_follow_up_glosas(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        limit=20,
        offset=0,
    )
    assert follow_up['valor_total_pendente'] == Decimal('10.00')
    assert follow_up['valor_total_tratado'] == Decimal('10.00')
    assert len(follow_up['cards']) == 1
    itens_reexibidos = [
        item
        for paciente in follow_up['cards'][0]['pacientes']
        for item in paciente['itens']
    ]
    item_restaurado = next(
        item
        for item in itens_reexibidos
        if item['registro_glosa'].id == registros_glosa[0].id
    )
    assert item_restaurado['registro_glosa'].status_tratativa == 'pendente'


def test_follow_up_pagina_por_processo_sem_separar_suas_remessas(
    session,
    usuario_teste,
    monkeypatch,
):
    primeira_remessa = criar_conciliacao_anterior_com_glosa(
        session,
        usuario_teste.id,
        cd_remessa=987,
        valor_total='100.00',
        valor_glosado='20.00',
    )
    segunda_remessa = ConciliacaoFaturamentoRemessa(
        conciliacao_id=primeira_remessa.conciliacao_id,
        cd_remessa=988,
        convenio='Convenio Teste',
        cnpj_convenio='98765432000110',
        valor_total=Decimal('200.00'),
        sn_glosado='true',
        valor_glosado=Decimal('30.00'),
        tp_conciliacao='faturamento',
    )
    session.add(segunda_remessa)
    remessa_financeira = RemessaFinanceira(
        cd_remessa=988,
        convenio='Convenio Teste',
        cnpj_convenio='98765432000110',
        valor_total=Decimal('200.00'),
        recebimento_integral=False,
    )
    remessa_financeira.data_registro = datetime(2026, 6, 10, 10, 0)
    session.add(remessa_financeira)
    session.commit()
    monkeypatch.setattr(
        financeiro,
        '_sincronizar_itens_follow_up',
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        financeiro,
        'sincronizar_totais_remessas_financeiras',
        lambda *_args, **_kwargs: pytest.fail(
            'A listagem resumida não deve sincronizar totais no Oracle.'
        ),
    )
    monkeypatch.setattr(
        financeiro,
        '_numeros_protocolo_por_remessa_follow_up',
        lambda *_args, **_kwargs: {
            987: '6094970',
            988: '6094971, 6094972',
        },
    )

    follow_up = financeiro.consultar_follow_up_glosas(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        limit=1,
        offset=0,
        incluir_detalhes=False,
        agrupar_por_processo=True,
    )

    assert follow_up['total'] == 1
    assert follow_up['valor_total_glosado'] == Decimal('50.00')
    assert {card['cd_remessa'] for card in follow_up['cards']} == {987, 988}
    assert {
        card['cd_remessa']: card['valor_itens']
        for card in follow_up['cards']
    } == {
        987: Decimal('100.00'),
        988: Decimal('200.00'),
    }
    assert {
        card['processo']['numero_processo'] for card in follow_up['cards']
    } == {'PROC-ANTERIOR'}
    assert {
        card['cd_remessa']: card['numero_protocolo']
        for card in follow_up['cards']
    } == {
        987: '6094970',
        988: '6094971, 6094972',
    }


def test_follow_up_limita_totalizadores_ao_valor_glosado(
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
    registro = session.scalar(
        select(RegistroGlosa).order_by(RegistroGlosa.id)
    )
    registro.valor_recursado = Decimal('50.00')
    session.commit()

    follow_up = financeiro.consultar_follow_up_glosas(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        limit=20,
        offset=0,
    )

    assert follow_up['valor_total_glosado'] == Decimal('20.00')
    assert follow_up['valor_total_tratado'] == Decimal('20.00')
    assert follow_up['valor_total_pendente'] == Decimal('0.00')


def test_follow_up_inclui_card_da_cogestao_sem_demonstrativo(
    session,
    usuario_teste,
    monkeypatch,
):
    monkeypatch.setattr(
        financeiro,
        '_sincronizar_itens_follow_up',
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        financeiro,
        '_cards_cogestao_follow_up',
        lambda *_args, **_kwargs: [{
            'conciliacao_remessa_id': None,
            'cd_remessa': 19001,
            'convenio': 'IPM',
            'data_competencia': date(2026, 5, 1),
            'data_entrega': None,
            'numero_nfse': '',
            'valor_remessa': Decimal('500.00'),
            'valor_itens': Decimal('500.00'),
            'valor_glosado': Decimal('37.50'),
            'valor_glosa_pendente': Decimal('37.50'),
            'valor_total_tratado': Decimal('0.00'),
            'processo': {
                'numero_processo': 'P249767/2026',
                'data_abertura': date(2026, 6, 10),
                'status_processo': 'TRAMITANDO',
                'motivo_finalizacao': None,
            },
            'recebimentos': [],
            'fiscal': {
                'numero_nfse': '',
                'valor_servicos': Decimal('0.00'),
                'impostos': Decimal('0.00'),
                'valor_liquido_nfse': Decimal('0.00'),
                'data_emissao': None,
            },
            'pacientes': [],
        }],
    )

    follow_up = financeiro.consultar_follow_up_glosas(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        limit=20,
        offset=0,
        incluir_detalhes=False,
        agrupar_por_processo=True,
    )

    assert follow_up['total'] == 1
    assert follow_up['valor_total_glosado'] == Decimal('37.50')
    assert follow_up['valor_total_pendente'] == Decimal('37.50')
    assert follow_up['cards'][0]['conciliacao_remessa_id'] is None
    assert follow_up['cards'][0]['valor_itens'] == Decimal('500.00')
    assert follow_up['cards'][0]['processo']['status_processo'] == 'TRAMITANDO'
    assert follow_up['cards'][0]['pacientes'] == []


def test_relatorios_dos_dois_status_montam_remessa_paciente_e_item(
    monkeypatch,
):
    queries = []

    class Resultado:
        def mappings(self):
            return self

        def all(self):
            base = {
                'numero_processo': 'P335842/2026',
                'cd_remessa': REMESSA_RELATORIO_TRAMITANDO,
                'competencia': date(2026, 5, 1),
                'valor_conta_relatorio': Decimal('1234.56'),
                'criterio_conta': 'remessa_conta_atendimento',
                'conta': CONTA_RELATORIO_TRAMITANDO,
                'cd_atendimento': ATENDIMENTO_RELATORIO_TRAMITANDO,
                'cd_paciente': 42,
                'nm_paciente': 'MARIA DA SILVA',
                'cd_prestador': 99,
                'nm_prestador': 'HOSPITAL PRONTOCARDIO',
                'cd_convenio': 10,
                'nm_convenio': 'IPM',
                'tp_atendimento': 'Internação',
                'nr_guia': '778899',
                'numero_lote': 'TISS_0000123_4207',
                'numero_protocolo': 'PROTOCOLO-1',
                'dt_atendimento': datetime(2026, 5, 10, 8, 0),
                'dt_alta': datetime(2026, 5, 11, 10, 0),
                'dt_lancamento': datetime(2026, 5, 10, 9, 0),
                'qt_lancamento': Decimal('1.00'),
                'cd_gru_fat': 1,
                'ds_gru_fat': 'DIÁRIAS',
                'cd_gru_pro': 2,
                'ds_gru_pro': 'INTERNAÇÃO',
                'data_abertura': date(2026, 8, 5),
                'status_processo': 'TRAMITANDO',
                'motivo_finalizacao': None,
            }
            return [
                {
                    **base,
                    'id_item_relatorio': 'item-1',
                    'cd_lancamento': LANCAMENTO_RELATORIO_TRAMITANDO,
                    'cd_pro_fat': 'PROC-1',
                    'cd_tuss': 'TUSS-1',
                    'descricao': 'Diária hospitalar',
                    'valor_item': Decimal('300.00'),
                    'numero_protocolo': 'PROTOCOLO-1',
                    'codigo_servico': 'TUSS-1',
                    'codigo_glosa': '1305',
                    'codigo_beneficiario': '00042',
                    'referencia': date(2026, 6, 1),
                    'valor_protocolo': Decimal('1234.56'),
                    'valor_glosa_protocolo': Decimal('55.00'),
                    'valor_processado': Decimal('300.00'),
                    'valor_liberado': Decimal('245.00'),
                    'valor_glosa': Decimal('55.00'),
                    'data_realizacao': date(2026, 5, 10),
                    'criterio_demonstrativo': (
                        'relatorio_hpc_conta_guia_servico'
                    ),
                    'descricao_glosa': 'Conta sem assinatura',
                },
                {
                    **base,
                    'id_item_relatorio': 'item-2',
                    'cd_lancamento': 102,
                    'cd_pro_fat': 'PROC-2',
                    'cd_tuss': None,
                    'descricao': 'Material hospitalar',
                    'valor_item': Decimal('934.56'),
                    'numero_protocolo': None,
                    'codigo_servico': None,
                    'codigo_glosa': None,
                    'codigo_beneficiario': None,
                    'referencia': None,
                    'valor_protocolo': None,
                    'valor_glosa_protocolo': None,
                    'valor_processado': None,
                    'valor_liberado': None,
                    'valor_glosa': None,
                    'data_realizacao': None,
                    'criterio_demonstrativo': None,
                    'descricao_glosa': None,
                },
            ]

    class Sessao:
        def execute(self, query):
            queries.append(str(query))
            return Resultado()

    monkeypatch.setattr(
        financeiro,
        '_tabela_ipm_existe',
        lambda *_args: True,
    )
    monkeypatch.setattr(
        financeiro,
        '_tratativas_demonstrativo_por_item',
        lambda *_args: {
            (
                'p335842/2026',
                REMESSA_RELATORIO_TRAMITANDO,
                ATENDIMENTO_RELATORIO_TRAMITANDO,
                CONTA_RELATORIO_TRAMITANDO,
                LANCAMENTO_RELATORIO_TRAMITANDO,
            ): [
                SimpleNamespace(
                    id=99,
                    sn_ativo='true',
                    status_tratativa='recurso',
                    valor_recursado=Decimal('25.00'),
                    motivo_glosa='1305',
                )
            ]
        },
    )

    cards = financeiro._cards_relatorios_follow_up(
        Sessao(),
        set(),
        q=None,
        cd_remessa=None,
        convenio=None,
        processo_original=None,
        paciente=None,
        cd_atendimento=None,
        tipo_atendimento=None,
    )

    assert len(cards) == 1
    assert cards[0]['cd_remessa'] == REMESSA_RELATORIO_TRAMITANDO
    assert cards[0]['valor_itens'] == Decimal('300.00')
    assert cards[0]['valor_remessa'] == Decimal('1234.56')
    assert cards[0]['valor_glosado'] == Decimal('55.00')
    assert cards[0]['numero_protocolo'] == 'PROTOCOLO-1'
    assert cards[0]['processo']['status_processo'] == 'TRAMITANDO'
    assert cards[0]['possui_recurso'] is True
    itens = cards[0]['pacientes'][0]['itens']
    assert len(itens) == 1
    item = itens[0]
    assert item['nr_guia'] == '778899'
    assert item['cd_reg'] == CONTA_RELATORIO_TRAMITANDO
    assert item['cd_atendimento'] == ATENDIMENTO_RELATORIO_TRAMITANDO
    assert item['cd_lancamento'] == LANCAMENTO_RELATORIO_TRAMITANDO
    assert item['descricao'] == 'Diária hospitalar'
    assert item['numero_protocolo'] == 'PROTOCOLO-1'
    assert item['codigo_beneficiario'] == '00042'
    assert item['valor_glosa'] == Decimal('55.00')
    assert item['valor_total_tratado'] == Decimal('25.00')
    assert item['tratativa_disponivel'] is True
    assert "IN ('FINALIZADO', 'TRAMITANDO')" in queries[0]
    assert '::integer >= 2024' in queries[0]
    assert 'COALESCE(item.valor_glosa, 0) > 0' in queries[0]

    paciente_demonstrativo = {
        'codigo_paciente': 42,
        'nm_paciente': 'MARIA DA SILVA',
        'valor_itens': Decimal('600.00'),
        'valor_glosado': Decimal('70.00'),
        'valor_total_tratado': Decimal('0.00'),
        'itens': [{'descricao': 'Item ausente no relatório'}],
    }
    chamadas_demonstrativo = []

    def pacientes_demonstrativo(*args):
        chamadas_demonstrativo.append(args)
        return [paciente_demonstrativo]

    monkeypatch.setattr(
        financeiro,
        '_pacientes_demonstrativo_conciliado',
        pacientes_demonstrativo,
    )
    cards = financeiro._cards_relatorios_follow_up(
        Sessao(),
        set(),
        session_oracle=object(),
        q=None,
        cd_remessa=None,
        convenio=None,
        processo_original=None,
        paciente=None,
        cd_atendimento=None,
        tipo_atendimento=None,
    )

    assert cards[0]['pacientes'] == [paciente_demonstrativo]
    assert cards[0]['valor_glosado'] == Decimal('70.00')
    assert cards[0]['valor_glosa_pendente'] == Decimal('70.00')
    assert chamadas_demonstrativo[0][2:] == (
        REMESSA_RELATORIO_TRAMITANDO,
        'P335842/2026',
        Decimal('1234.56'),
        Decimal('55.00'),
        'PROTOCOLO-1',
    )


def test_follow_up_pagina_processos_por_competencia_mais_recente(
    session,
    usuario_teste,
    monkeypatch,
):
    monkeypatch.setattr(
        financeiro,
        '_sincronizar_itens_follow_up',
        lambda *_args, **_kwargs: 0,
    )

    def card(numero_processo, cd_remessa, competencia):
        return {
            'conciliacao_remessa_id': None,
            'cd_remessa': cd_remessa,
            'convenio': 'IPM',
            'data_competencia': competencia,
            'data_entrega': None,
            'numero_nfse': '',
            'valor_remessa': Decimal('500.00'),
            'valor_itens': Decimal('500.00'),
            'valor_glosado': Decimal('10.00'),
            'valor_glosa_pendente': Decimal('6.00'),
            'valor_total_tratado': Decimal('4.00'),
            'processo': {
                'numero_processo': numero_processo,
                'data_abertura': None,
                'status_processo': 'FINALIZADO',
                'motivo_finalizacao': None,
            },
            'recebimentos': [],
            'fiscal': {
                'numero_nfse': '',
                'valor_servicos': Decimal('0.00'),
                'impostos': Decimal('0.00'),
                'valor_liquido_nfse': Decimal('0.00'),
                'data_emissao': None,
            },
            'pacientes': [],
        }

    monkeypatch.setattr(
        financeiro,
        '_cards_cogestao_follow_up',
        lambda *_args, **_kwargs: [
            card('P-ANTIGO', 19001, date(2026, 4, 1)),
            card('P-RECENTE', 19002, date(2026, 7, 1)),
        ],
    )

    follow_up = financeiro.consultar_follow_up_glosas(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        limit=1,
        offset=0,
        incluir_detalhes=False,
        agrupar_por_processo=True,
    )

    assert follow_up['total'] == len({'P-ANTIGO', 'P-RECENTE'})
    assert follow_up['valor_total_glosado'] == Decimal('20.00')
    assert follow_up['valor_total_pendente'] == Decimal('12.00')
    assert follow_up['valor_total_tratado'] == Decimal('8.00')
    assert len(follow_up['cards']) == 1
    assert (
        follow_up['cards'][0]['processo']['numero_processo']
        == 'P-RECENTE'
    )


def test_cards_cogestao_incluem_remessa_sem_glosa_ao_filtrar_processo(
    monkeypatch,
):
    consultas = []
    cd_remessa = 16303

    class Resultado:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def __iter__(self):
            return iter(self.rows)

    class Sessao:
        def execute(self, query, params=None):
            consultas.append((str(query), params))
            if 'processos_ipm_saude_cogestao AS cog' in str(query):
                return Resultado([
                    {
                        'numero_processo': 'P058752/2026',
                        'nr': '4123928',
                        'competencia_producao': '12/2025',
                        'valor_protocolo': Decimal('7298.14'),
                        'valor_glosado_protocolo': Decimal('0.00'),
                        'data_fechamento': None,
                        'data_abertura': date(2026, 2, 9),
                        'status_processo': 'FINALIZADO',
                        'motivo_finalizacao': None,
                        'cd_remessa_manual': None,
                    },
                    {
                        'numero_processo': 'P058752/2026',
                        'nr': 'SEM-VINCULO',
                        'competencia_producao': '12/2025',
                        'valor_protocolo': Decimal('999.99'),
                        'valor_glosado_protocolo': Decimal('10.00'),
                        'data_fechamento': None,
                        'data_abertura': date(2026, 2, 9),
                        'status_processo': 'FINALIZADO',
                        'motivo_finalizacao': None,
                        'cd_remessa_manual': None,
                    },
                ])
            return Resultado([])

        def scalar(self, _query):
            return None

        def scalars(self, _query):
            return []

    monkeypatch.setattr(
        financeiro,
        '_tabela_ipm_existe',
        lambda _session, tabela: (
            tabela != 'associacoes_remessas_ipm_manuais'
        ),
    )
    monkeypatch.setattr(
        financeiro,
        '_remessas_cogestao_persistidas',
        lambda *_args: {
            Decimal('7298.14'): [{
                'cd_remessa': cd_remessa,
                'cnpj_convenio': '',
                'convenio': 'IPM',
                'valor_total': Decimal('7298.14'),
                'data_competencia': date(2025, 12, 1),
            }]
        },
    )
    monkeypatch.setattr(
        financeiro,
        '_complementar_remessas_cogestao',
        lambda *_args, **_kwargs: pytest.fail(
            'A listagem resumida não deve consultar remessas no MV.'
        ),
    )
    monkeypatch.setattr(
        financeiro,
        '_tratativas_demonstrativo_por_item',
        lambda *_args: {},
    )
    monkeypatch.setattr(
        financeiro,
        '_cards_demonstrativo_processos_abertos',
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        financeiro,
        '_cards_relatorios_follow_up',
        lambda *_args, **_kwargs: [],
    )

    cards = financeiro._cards_cogestao_follow_up(
        Sessao(),
        object(),
        set(),
        incluir_detalhes=False,
        q=None,
        numero_nfse=None,
        cd_remessa=None,
        convenio=None,
        processo_original='P058752/2026',
        processo_recurso=None,
        paciente=None,
        cd_atendimento=None,
        tipo_atendimento=None,
    )

    assert len(cards) == 1
    assert cards[0]['cd_remessa'] == cd_remessa
    assert cards[0]['numero_protocolo'] == '4123928'
    assert cards[0]['valor_glosado'] == Decimal('0.00')
    assert cards[0]['valor_total_tratado'] == Decimal('0.00')
    assert cards[0]['valor_glosa_pendente'] == Decimal('0.00')
    assert consultas[0][1] == {
        'processo_sem_glosa': 'P058752/2026',
        'processo_sem_glosa_like': '%P058752/2026%',
    }


def test_associacao_manual_inclui_remessa_indicada_pelo_outro_portal(
    monkeypatch,
):
    class Resultado:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def all(self):
            return self.rows

        def __iter__(self):
            return iter(self.rows)

    class Sessao:
        def execute(self, query, params=None):
            consulta = str(query)
            if 'WITH chaves_pendentes AS' in consulta:
                return Resultado([{
                    'numero_processo_normalizado': 'P058752/2026',
                    'competencia_producao': '12/2025',
                    'nr': '4124085',
                    'numero_processo': 'P058752/2026',
                    'valor_informado': Decimal('14754.69'),
                    'valor_aprovado_producao': Decimal('14660.90'),
                    'valor_glosado_producao': Decimal('93.79'),
                    'valor_protocolado_nr': Decimal('5846.55'),
                    'valor_aprovado_nr': Decimal('5822.76'),
                    'valor_glosado_nr': Decimal('0.92'),
                    'data_abertura': date(2026, 2, 9),
                    'status_processo': 'FINALIZADO',
                }])
            if 'AS item_oracle' in consulta:
                assert params == {'processos': ['P058752/2026']}
                return Resultado([{
                    'id_registro': 'registro-1',
                    'cd_remessa': 16425,
                    'conta': 343332,
                    'cd_lancamento': 9,
                    'cd_atendimento': 279139,
                    'cd_paciente': 107036,
                    'nm_paciente': 'PHABYANE FRANCA RIBEIRO',
                    'nr_guia': '363150',
                    'cd_pro_fat': '90222377',
                    'cd_tuss': '',
                    'descricao': 'CLORETO SODIO 0,9% BOLS C/100ML',
                    'dt_atendimento': date(2025, 12, 21),
                    'nm_prestador': None,
                    'valor_item': Decimal('10.95'),
                }])
            if 'SELECT DISTINCT' in consulta and (
                'processos_relatorios_itens_ipm' in consulta
            ):
                if params == {'remessas': [16425]}:
                    return Resultado([])
                assert params == {'processos': ['P058752/2026']}
                return Resultado([{
                    'numero_processo_normalizado': 'P058752/2026',
                    'nr': '4124085',
                    'cd_remessa': 16425,
                }])
            if 'glossas_nao_vinculadas_ipm' in consulta and (
                'AS pendencia' in consulta
            ):
                assert params == {'processos': ['P058752/2026']}
                return Resultado([{
                    'id_registro': 'registro-1',
                    'numero_processo_normalizado': 'P058752/2026',
                    'competencia_producao': '12/2025',
                    'nr': '4124085',
                    'data_realizacao': date(2025, 12, 21),
                    'numero_guia_senha': '389690',
                    'codigo_beneficiario': '1106530000',
                    'codigo_servico': '90222377',
                    'codigo_glosa': '1714',
                    'valor_processado': Decimal('10.95'),
                    'valor_glosa': Decimal('0.88'),
                }])
            if 'ipm_remessas_oracle AS rem' in consulta:
                assert params == {
                    'competencias': ['12/2025'],
                    'codigos_remessas_portal': [16425],
                }
                return Resultado([{
                    'cd_remessa': 16425,
                    'competencia': '01/2026',
                    'nm_convenio': 'IPM',
                    'valor_total': Decimal('5846.55'),
                    'associacao_id': None,
                    'processo_associado': None,
                    'competencia_associada': None,
                    'nr_associado': None,
                    'vinculada_automaticamente': False,
                }])
            raise AssertionError(consulta)

    monkeypatch.setattr(
        financeiro,
        '_tabela_ipm_existe',
        lambda *_args: True,
    )
    monkeypatch.setattr(
        financeiro,
        '_tabela_schema_existe',
        lambda *_args: True,
    )

    resultado = financeiro.consultar_associacoes_remessas_ipm(
        usuario_atual=object(),
        session=Sessao(),
        competencia=None,
        numero_processo='P058752/2026',
        limit=10,
        offset=0,
    )

    nr = resultado['processos'][0]['nrs'][0]
    assert nr['valor_glosado'] == Decimal('0.92')
    assert nr['registros_pendentes'] == [{
        'id_registro': 'registro-1',
        'data_realizacao': date(2025, 12, 21),
        'numero_guia_senha': '389690',
        'codigo_beneficiario': '1106530000',
        'codigo_servico': '90222377',
        'codigo_glosa': '1714',
        'valor_processado': Decimal('10.95'),
        'valor_glosa': Decimal('0.88'),
        'correspondencias_oracle': [{
            'cd_remessa': 16425,
            'conta': 343332,
            'cd_lancamento': 9,
            'cd_atendimento': 279139,
            'cd_paciente': 107036,
            'nm_paciente': 'PHABYANE FRANCA RIBEIRO',
            'nr_guia': '363150',
            'cd_pro_fat': '90222377',
            'cd_tuss': '',
            'descricao': 'CLORETO SODIO 0,9% BOLS C/100ML',
            'dt_atendimento': date(2025, 12, 21),
            'nm_prestador': None,
            'valor_item': Decimal('10.95'),
        }],
        'correspondencia_unica': True,
        'associacao': None,
    }]
    assert nr['associacoes_itens'] == 0
    assert nr['remessas'] == [{
        'cd_remessa': 16425,
        'competencia': '01/2026',
        'nm_convenio': 'IPM',
        'valor_total': Decimal('5846.55'),
        'associacao_id': None,
        'processo_associado': None,
        'competencia_associada': None,
        'nr_associado': None,
        'vinculada_automaticamente': False,
        'indicada_pelo_portal': True,
    }]


def test_associacao_item_manual_gera_linha_para_follow_up(monkeypatch):
    class Resultado:
        def mappings(self):
            return self

        def all(self):
            associacao = {
                'associacao_id': 7,
                'associacao_cd_remessa': 16425,
                'associacao_conta': 343332,
                'associacao_cd_lancamento': 9,
                'criterio_correspondencia': (
                    'atendimento_guia_servico_carteira_valor'
                ),
                'numero_processo': 'P058752/2026',
                'numero_protocolo': '4124085',
                'numero_lote': None,
                'codigo_servico': '90222377',
                'codigo_glosa': '1714',
                'codigo_beneficiario': '1106530000',
                'referencia': date(2026, 1, 1),
                'valor_protocolo': Decimal('5846.55'),
                'valor_glosa_protocolo': Decimal('23.79'),
                'valor_processado': Decimal('10.95'),
                'valor_liberado': Decimal('10.07'),
                'valor_glosa': Decimal('0.88'),
                'data_realizacao': date(2025, 12, 21),
                'descricao_glosa': 'Cobrança em desacordo',
                'data_abertura': date(2026, 2, 9),
                'status_processo': 'FINALIZADO',
                'motivo_finalizacao': None,
            }
            # O portal pode manter mais de uma linha do mesmo processo.
            # A associação deve compor o Follow-Up uma única vez.
            return [associacao, dict(associacao)]

    class Sessao:
        def execute(self, _query):
            return Resultado()

    item_oracle = {
        'cd_remessa': 16425,
        'cd_reg': 343332,
        'cd_lancamento': 9,
        'cd_atendimento': 279139,
        'cd_paciente': 107036,
        'nm_paciente': 'PHABYANE FRANCA RIBEIRO',
        'cd_prestador': 1,
        'nm_prestador': 'HOSPITAL PRONTOCARDIO',
        'cd_convenio': 10,
        'nm_convenio': 'IPM',
        'tp_atendimento': 'Externo',
        'nr_guia': '363150',
        'cd_pro_fat': '90222377',
        'cd_tuss': None,
        'descricao': 'CLORETO SODIO',
        'dt_atendimento': date(2025, 12, 21),
        'dt_alta': None,
        'dt_competencia': date(2026, 1, 1),
        'dt_lancamento': date(2025, 12, 21),
        'qt_lancamento': Decimal('1'),
        'vl_total_conta': Decimal('10.95'),
        'vl_total_registro': Decimal('5846.55'),
        'cd_gru_fat': 1,
        'ds_gru_fat': 'MEDICAMENTOS',
        'cd_gru_pro': 1,
        'ds_gru_pro': 'MEDICAMENTOS',
    }
    monkeypatch.setattr(financeiro, '_tabela_ipm_existe', lambda *_: True)
    monkeypatch.setattr(
        financeiro,
        '_itens_oracle_remessas_ipm',
        lambda *_: (
            {
                16425: {
                    'data_competencia': date(2026, 1, 1),
                }
            },
            {16425: [item_oracle]},
        ),
    )

    linhas = financeiro._linhas_associacoes_itens_manuais_follow_up(
        Sessao(), object()
    )

    assert len(linhas) == 1
    assert linhas[0]['id_item_relatorio'] == 'manual:7'
    assert linhas[0]['valor_glosa'] == Decimal('0.88')
    assert linhas[0]['conta'] == CONTA_ASSOCIACAO_MANUAL
    assert linhas[0]['cd_lancamento'] == LANCAMENTO_ASSOCIACAO_MANUAL


def test_associacao_manual_substitui_correspondencia_automatica_no_follow_up():
    item_automatico = ({'id_registro': 'registro-1'}, object())
    item_nao_associado = ({'id_registro': 'registro-2'}, object())
    item_manual = (
        {'id_registro': 'registro-1'},
        {'cd_reg': 343332, 'cd_lancamento': 9},
        'associacao_manual',
    )

    automaticos = (
        financeiro._remover_correspondencias_automaticas_associadas_manualmente(
            [item_automatico, item_nao_associado],
            [item_manual],
        )
    )

    assert automaticos == [item_nao_associado]


def test_cria_edita_e_exclui_associacao_manual_de_item(monkeypatch):
    class Sessao:
        def __init__(self):
            self.objeto = None
            self.excluido = None

        def add(self, objeto):
            self.objeto = objeto

        def commit(self):
            if self.objeto is not None and self.objeto.id is None:
                self.objeto.id = 7

        def refresh(self, _objeto):
            return None

        def get(self, _modelo, _id):
            return self.objeto

        def delete(self, objeto):
            self.excluido = objeto

    pendencia = {
        'id_registro': 'registro-1',
        'numero_processo': 'P058752/2026',
        'numero_protocolo': '4124085',
        'data_realizacao': date(2025, 12, 21),
    }
    monkeypatch.setattr(
        financeiro,
        '_validar_associacao_item_manual',
        lambda *_args, **_kwargs: (
            pendencia,
            'atendimento_guia_servico_carteira_valor',
        ),
    )
    session = Sessao()
    usuario = SimpleNamespace(id=3)

    criada = financeiro.criar_associacao_item_ipm(
        AssociacaoItemIpmManualCreate(
            glosa_id_registro='registro-1',
            cd_remessa=16425,
            conta=343332,
            cd_lancamento=9,
        ),
        usuario,
        session,
        object(),
    )

    assert isinstance(criada, AssociacaoItemIpmManual)
    assert criada.id == ID_ASSOCIACAO_MANUAL
    assert criada.glosa_id_registro == 'registro-1'
    assert criada.competencia_producao == '12/2025'

    editada = financeiro.editar_associacao_item_ipm(
        7,
        AssociacaoItemIpmManualUpdate(
            cd_remessa=16426,
            conta=343333,
            cd_lancamento=10,
        ),
        usuario,
        session,
        object(),
    )

    assert editada.cd_remessa == REMESSA_ASSOCIACAO_MANUAL_EDITADA
    assert editada.conta == CONTA_ASSOCIACAO_MANUAL_EDITADA
    assert (
        editada.cd_lancamento == LANCAMENTO_ASSOCIACAO_MANUAL_EDITADO
    )

    financeiro.excluir_associacao_item_ipm(7, usuario, session)
    assert session.excluido is editada


def test_validacao_aceita_competencia_distinta_indicada_pelo_portal(
    monkeypatch,
):
    class Resultado:
        def mappings(self):
            return self

        def first(self):
            return {
                'cd_remessa': 16425,
                'competencia': '01/2026',
                'nm_convenio': 'IPM',
                'valor_total': Decimal('5846.55'),
            }

    class Sessao:
        def __init__(self):
            self.resultados_scalar = iter((True, True, None))

        def scalar(self, _query, _params=None):
            return next(self.resultados_scalar)

        def execute(self, _query, _params=None):
            return Resultado()

    monkeypatch.setattr(
        financeiro,
        '_tabela_ipm_existe',
        lambda _session, tabela: (
            tabela == 'processos_relatorios_itens_ipm'
        ),
    )
    monkeypatch.setattr(
        financeiro,
        '_tabela_schema_existe',
        lambda *_args: False,
    )

    chave = financeiro._validar_remessa_associacao_manual(
        Sessao(),
        numero_processo='p058752/2026',
        competencia_producao='12/2025',
        nr='4124085',
        cd_remessa=16425,
    )

    assert chave == ('P058752/2026', '12/2025', '4124085')


def test_relatorio_preserva_total_de_glosa_sinalizado_pelo_portal():
    card_relatorio = {
        'cd_remessa': 16425,
        'valor_glosado': Decimal('22.87'),
        'valor_glosa_pendente': Decimal('22.87'),
        'valor_total_tratado': Decimal('0.00'),
        'processo': {'numero_processo': 'P058752/2026'},
    }
    cards_cogestao = [{
        'cd_remessa': 16425,
        'valor_glosado': Decimal('23.79'),
        'processo': {'numero_processo': 'P058752/2026'},
    }]

    financeiro._preservar_totais_glosa_portal(
        [card_relatorio],
        cards_cogestao,
    )

    assert card_relatorio['valor_glosado'] == Decimal('23.79')
    assert card_relatorio['valor_glosa_pendente'] == Decimal('23.79')


def test_follow_up_prioriza_ipm_sobre_conciliacao_legada(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_conciliacao_anterior_com_glosa(
        session,
        usuario_teste.id,
        valor_glosado='20.00',
    )
    card_ipm = {
        'conciliacao_remessa_id': None,
        'cd_remessa': CD_REMESSA_TESTE,
        'numero_protocolo': 'PROTOCOLO-IPM',
        'convenio': 'IPM',
        'data_competencia': date(2026, 6, 1),
        'data_entrega': date(2026, 6, 10),
        'numero_nfse': '',
        'valor_remessa': Decimal('120.00'),
        'valor_itens': Decimal('100.00'),
        'valor_glosado': Decimal('55.00'),
        'valor_glosa_pendente': Decimal('45.00'),
        'valor_total_tratado': Decimal('10.00'),
        'possui_recurso': True,
        'processo': {
            'numero_processo': 'PROC-ANTERIOR',
            'data_abertura': date(2026, 6, 1),
            'status_processo': 'FINALIZADO',
            'motivo_finalizacao': None,
        },
        'recebimentos': [],
        'fiscal': {
            'numero_nfse': '',
            'valor_servicos': Decimal('0.00'),
            'impostos': Decimal('0.00'),
            'valor_liquido_nfse': Decimal('0.00'),
            'data_emissao': None,
        },
        'pacientes': [
            {
                'codigo_paciente': 1,
                'nm_paciente': 'Paciente IPM',
                'valor_itens': Decimal('100.00'),
                'valor_glosado': Decimal('55.00'),
                'valor_total_tratado': Decimal('10.00'),
                'itens': [{'cd_lancamento': 1}],
            }
        ],
    }
    monkeypatch.setattr(
        financeiro,
        '_cards_cogestao_follow_up',
        lambda *_args, **_kwargs: [card_ipm],
    )

    follow_up = financeiro.consultar_follow_up_glosas(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        numero_nfse=None,
        cd_remessa=None,
        convenio=None,
        processo_original='PROC-ANTERIOR',
        processo_recurso=None,
        paciente=None,
        cd_atendimento=None,
        tipo_atendimento=None,
        limit=20,
        offset=0,
        conciliacao_remessa_id=None,
        incluir_detalhes=False,
        agrupar_por_processo=True,
    )

    assert follow_up['cards'] == [card_ipm]
    assert follow_up['quantidade_glosas'] == 1
    assert follow_up['valor_total_glosado'] == Decimal('55.00')
    assert follow_up['valor_total_pendente'] == Decimal('45.00')
    assert follow_up['valor_total_tratado'] == Decimal('10.00')


def test_follow_up_exibe_registro_analitico_sem_conciliacao_ou_relatorio(
    session,
    usuario_teste,
    monkeypatch,
):
    cd_remessa_orfa = 999
    criar_recurso_aberto(
        session,
        cd_remessa=cd_remessa_orfa,
        conciliacao_remessa_id=None,
        processo_controle_fatura_gab='PROC-ORFAO',
        processo_recurso=None,
        valor_recursado='0.00',
        valor=Decimal('20.00'),
        dt_recurso=None,
        dt_pagamento=None,
    )
    monkeypatch.setattr(
        financeiro,
        '_cards_cogestao_follow_up',
        lambda *_args, **_kwargs: [],
    )

    follow_up = financeiro.consultar_follow_up_glosas(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        numero_nfse=None,
        cd_remessa=None,
        convenio=None,
        processo_original='PROC-ORFAO',
        processo_recurso=None,
        paciente=None,
        cd_atendimento=None,
        tipo_atendimento=None,
        limit=20,
        offset=0,
        conciliacao_remessa_id=None,
        incluir_detalhes=False,
        agrupar_por_processo=True,
    )

    assert follow_up['total'] == 1
    assert follow_up['quantidade_glosas'] == 1
    assert follow_up['valor_total_glosado'] == Decimal('20.00')
    assert follow_up['cards'][0]['cd_remessa'] == cd_remessa_orfa
    assert follow_up['cards'][0]['processo']['numero_processo'] == 'PROC-ORFAO'
    assert follow_up['cards'][0]['conciliacao_remessa_id'] is None
    CardFollowUpGlosaPublic.model_validate(follow_up['cards'][0])


def test_follow_up_nao_cria_itens_sem_demonstrativo(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_conciliacao_anterior_com_glosa(
        session,
        usuario_teste.id,
    )

    def falhar_se_consultar_oracle(*_args, **_kwargs):
        raise AssertionError(
            'O Oracle não deve detalhar glosa sem demonstrativo'
        )

    monkeypatch.setattr(
        financeiro,
        '_carregar_itens_glosa_conciliacao',
        falhar_se_consultar_oracle,
    )

    assert financeiro._sincronizar_itens_follow_up(
        session,
        object(),
    ) == 0
    assert session.scalar(select(RegistroGlosa.id)) is None


def test_follow_up_usa_total_registro_no_card_e_total_conta_nos_itens(
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
    monkeypatch.setattr(
        financeiro,
        'sincronizar_totais_remessas_financeiras',
        lambda *_args, **_kwargs: {
            CD_REMESSA_TESTE: Decimal('135.00')
        },
    )
    conciliacao_remessa_id = session.scalar(
        select(ConciliacaoFaturamentoRemessa.id).where(
            ConciliacaoFaturamentoRemessa.cd_remessa == CD_REMESSA_TESTE
        )
    )

    follow_up = financeiro.consultar_follow_up_glosas(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        limit=20,
        offset=0,
        conciliacao_remessa_id=conciliacao_remessa_id,
    )

    card = follow_up['cards'][0]
    valores_itens = [
        item['vl_total_conta']
        for paciente in card['pacientes']
        for item in paciente['itens']
    ]
    assert card['valor_remessa'] == Decimal('135.00')
    assert card['data_competencia'] == date(2026, 7, 1)
    assert card['valor_itens'] == sum(valores_itens, Decimal('0.00'))
    assert set(valores_itens) == {Decimal('60.00')}


def test_follow_up_agrupa_recurso_e_acato_no_mesmo_item(
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
    registro = session.scalar(
        select(RegistroGlosa).where(RegistroGlosa.cd_lancamento == 1)
    )
    app_glosas.editar_glosa(
        registro.id,
        payload_tratativa(registro, 'REC-ITEM-1', '10.00'),
        usuario_atual=usuario_teste,
        session=session,
    )
    dados_acato = payload_tratativa(
        registro,
        'TEMPORARIO',
        '5.00',
    ).model_dump()
    dados_acato.update(
        sn_glosado='not',
        processo_recurso=None,
        qtd_recursado=None,
    )
    acato = app_glosas.editar_glosa(
        registro.id,
        RegistroGlosaCreate(**dados_acato),
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
    itens = [
        item
        for paciente in follow_up['cards'][0]['pacientes']
        for item in paciente['itens']
    ]
    item = next(item for item in itens if item['cd_lancamento'] == 1)

    assert len(itens) == ITENS_ANALITICOS_TESTE
    assert item['registro_recusa'].id == registro.id
    assert item['registro_acato'].id == acato.id
    assert item['registro_recusa'].valor_recursado == Decimal('10.00')
    assert item['registro_acato'].valor_recursado == Decimal('5.00')
    assert follow_up['valor_total_tratado'] == Decimal('15.00')
    assert follow_up['valor_total_pendente'] == Decimal('5.00')


def test_follow_up_mantem_card_sem_criar_itens_fora_do_demonstrativo(
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
    assert follow_up['cards'][0]['pacientes'] == []
    registros = session.scalars(
        select(RegistroGlosa).where(
            RegistroGlosa.conciliacao_remessa_id == vinculo.id
        )
    ).all()
    assert registros == []

    financeiro.consultar_follow_up_glosas(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        limit=20,
        offset=0,
    )
    assert session.scalars(
        select(RegistroGlosa).where(
            RegistroGlosa.conciliacao_remessa_id == vinculo.id
        )
    ).all() == []


def test_follow_up_exibe_demonstrativo_conciliado_sem_materializar(
    session,
    usuario_teste,
    monkeypatch,
):
    configurar_oracle_fake(monkeypatch)
    vinculo = criar_conciliacao_anterior_com_glosa(
        session,
        usuario_teste.id,
    )
    paciente_demonstrativo = {
        'codigo_paciente': 1,
        'nm_paciente': 'Paciente Demonstrativo',
        'valor_itens': Decimal('60.00'),
        'valor_glosado': Decimal('20.00'),
        'valor_total_tratado': Decimal('0.00'),
        'itens': [],
    }
    chamadas_demonstrativo = []

    def pacientes_demonstrativo(*args):
        chamadas_demonstrativo.append(args)
        return [paciente_demonstrativo]

    monkeypatch.setattr(
        financeiro,
        '_pacientes_demonstrativo_conciliado',
        pacientes_demonstrativo,
    )

    resumo = financeiro.consultar_follow_up_glosas(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        limit=20,
        offset=0,
    )

    assert resumo['cards'][0]['pacientes'] == []
    assert chamadas_demonstrativo == []

    follow_up = financeiro.consultar_follow_up_glosas(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        limit=20,
        offset=0,
        conciliacao_remessa_id=vinculo.id,
    )

    card = follow_up['cards'][0]
    assert card['pacientes'] == [paciente_demonstrativo]
    assert len(chamadas_demonstrativo) == 1
    assert card['valor_itens'] == Decimal('120.00')
    assert session.scalars(
        select(RegistroGlosa).where(
            RegistroGlosa.conciliacao_remessa_id == vinculo.id
        )
    ).all() == []


def test_follow_up_exibe_protocolo_cogestao_no_card_conciliado(
    session,
    usuario_teste,
    monkeypatch,
):
    configurar_oracle_fake(monkeypatch)
    criar_conciliacao_anterior_com_glosa(
        session,
        usuario_teste.id,
    )
    monkeypatch.setattr(
        financeiro,
        '_numeros_protocolo_por_remessa_follow_up',
        lambda *_args: {CD_REMESSA_TESTE: 'PROTOCOLO-AMBIGUO'},
    )
    monkeypatch.setattr(
        financeiro,
        '_numeros_protocolo_cogestao_follow_up',
        lambda *_args: {CD_REMESSA_TESTE: '5584772'},
    )

    follow_up = financeiro.consultar_follow_up_glosas(
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
        q=None,
        limit=20,
        offset=0,
        incluir_detalhes=False,
    )

    assert follow_up['cards'][0]['numero_protocolo'] == '5584772'


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
    assert response['valor_total_recebido'] == Decimal('0.00')
    assert response['valor_total_pendente'] == Decimal('100.00')
    remessa = response['conciliacoes'][0]
    assert remessa['cd_remessa'] == CD_REMESSA_TESTE
    assert remessa['situacao'] == 'sem_recebimento'
    assert remessa['quantidade_nfses_sem_recebimento'] == 1
    assert remessa['valor_remessa'] == Decimal('120.00')
    assert remessa['valor_total_glosas'] == Decimal('20.00')
    assert remessa['valor_total_impostos'] == Decimal('0.00')
    assert remessa['valor_liquido'] == Decimal('100.00')
    assert remessa['valor_recebido'] == Decimal('0.00')
    assert remessa['valor_pendente'] == Decimal('100.00')
    dias_em_atraso = max(
        (
            datetime.now(financeiro.ZoneInfo('America/Sao_Paulo')).date()
            - date(2026, 8, 10)
        ).days,
        0,
    )
    assert remessa['notas'] == [
        {
            'id': remessa['notas'][0]['id'],
            'numero_nfse': '12345',
            'tp_conciliacao': 'faturamento',
            'data_previsao_recebimento': date(2026, 8, 10),
            'data_criacao': remessa['notas'][0]['data_criacao'],
            'valor_nfse': Decimal('100.00'),
            'valor_vinculado_remessa': Decimal('120.00'),
            'valor_alocado_nfse': Decimal('100.00'),
            'valor_impostos': Decimal('0.00'),
            'valor_glosado': Decimal('20.00'),
            'valor_recebido': Decimal('0.00'),
            'valor_pendente': Decimal('100.00'),
            'situacao': 'sem_recebimento',
            'em_atraso': dias_em_atraso > 0,
            'dias_em_atraso': dias_em_atraso,
            'recebimentos': [],
        }
    ]
    ConciliacoesSemRecebimentoList.model_validate(response)


def test_fila_financeira_separa_liquido_impostos_glosa_e_recebimento(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='100.00')
    configurar_cards_oracle_fake(monkeypatch)
    monkeypatch.setattr(
        financeiro,
        '_consultar_itens_remessas_hpc',
        itens_remessas_hpc,
    )
    financeiro.conciliar_remessa_com_nfses(
        cd_remessa=CD_REMESSA_TESTE,
        payload=ConciliacaoRemessaCreate(
            processo_recebimento='PROC-VALORES-FINANCEIROS',
            notas=[
                {
                    'nfse_row_hash': 'nfse-1',
                    'valor_alocado': '80.00',
                    'valor_impostos': '10.00',
                    'valor_glosado': '30.00',
                    'data_previsao_recebimento': '2026-08-10',
                }
            ],
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )
    conciliacao = session.scalar(select(ConciliacaoFaturamento))
    financeiro.registrar_recebimento_remessa(
        payload=RecebimentoRemessaCreate(
            conciliacao_id=conciliacao.id,
            cd_remessa=CD_REMESSA_TESTE,
            numero_nfse='12345',
            data_recebimento='2026-07-10',
            valor_recebido='45.00',
            conta_bancaria_id=CONTA_BANCARIA_TESTE,
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=OracleComContaFake(),
    )

    response = financeiro.consultar_conciliacoes_sem_recebimento(
        usuario_atual=usuario_teste,
        session=session,
        q=str(CD_REMESSA_TESTE),
        limit=25,
        offset=0,
    )

    remessa = response['conciliacoes'][0]
    nota = remessa['notas'][0]
    assert remessa['valor_liquido'] == Decimal('80.00')
    assert remessa['valor_total_impostos'] == Decimal('10.00')
    assert remessa['valor_total_glosas'] == Decimal('30.00')
    assert remessa['valor_recebido'] == Decimal('45.00')
    assert remessa['valor_pendente'] == Decimal('35.00')
    assert nota['valor_alocado_nfse'] == Decimal('80.00')
    assert nota['valor_recebido'] == Decimal('45.00')
    assert nota['valor_pendente'] == Decimal('35.00')
    ConciliacoesSemRecebimentoList.model_validate(response)


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
        'valor_total_recebido': Decimal('0.00'),
        'valor_total_pendente': Decimal('0.00'),
        'limit': 100,
        'offset': 0,
    }


def test_fila_sem_recebimento_agrupa_nfses_por_remessa(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='100.00')
    criar_nfse(
        session,
        row_hash='nfse-2',
        valor='100.00',
        numero_nfse='67890',
    )
    configurar_cards_oracle_fake(monkeypatch)
    for row_hash, valor in (('nfse-1', '60.00'), ('nfse-2', '40.00')):
        financeiro.conciliar_remessa_com_nfses(
            cd_remessa=CD_REMESSA_TESTE,
            payload=ConciliacaoRemessaCreate(
                processo_recebimento='PROC-SEM-RECEBIMENTO',
                notas=[
                    {
                        'nfse_row_hash': row_hash,
                        'valor_alocado': valor,
                        'data_previsao_recebimento': '2026-08-10',
                    }
                ],
            ),
            usuario_atual=usuario_teste,
            session_postgres=session,
            session_oracle=object(),
        )

    response = financeiro.consultar_conciliacoes_sem_recebimento(
        usuario_atual=usuario_teste,
        session=session,
        q=str(CD_REMESSA_TESTE),
        limit=25,
        offset=0,
    )

    assert response['total'] == 1
    assert response['total_remessas_sem_recebimento'] == 1
    assert response['valor_total_recebido'] == Decimal('0.00')
    assert response['valor_total_pendente'] == Decimal('100.00')
    remessa = response['conciliacoes'][0]
    assert remessa['cd_remessa'] == CD_REMESSA_TESTE
    assert (
        remessa['quantidade_nfses_sem_recebimento']
        == CONCILIACOES_DISTRIBUIDAS
    )
    assert {nota['numero_nfse'] for nota in remessa['notas']} == {
        '12345',
        '67890',
    }
    ConciliacoesSemRecebimentoList.model_validate(response)

    response_por_nfse = financeiro.consultar_conciliacoes_sem_recebimento(
        usuario_atual=usuario_teste,
        session=session,
        q='12345',
        limit=25,
        offset=0,
    )
    assert response_por_nfse['total'] == 1
    assert response_por_nfse['valor_total_pendente'] == Decimal('100.00')
    assert (
        len(response_por_nfse['conciliacoes'][0]['notas'])
        == CONCILIACOES_DISTRIBUIDAS
    )

    response_filtros_separados = (
        financeiro.consultar_conciliacoes_sem_recebimento(
            usuario_atual=usuario_teste,
            session=session,
            q=None,
            numero_nfse='12345',
            cd_remessa=str(CD_REMESSA_TESTE),
            convenio='Convenio Teste',
            processo_recebimento='PROC-SEM-RECEBIMENTO',
            limit=25,
            offset=0,
        )
    )
    assert response_filtros_separados['total'] == 1
    assert (
        response_filtros_separados['conciliacoes'][0]['cd_remessa']
        == CD_REMESSA_TESTE
    )

    response_processo_inexistente = (
        financeiro.consultar_conciliacoes_sem_recebimento(
            usuario_atual=usuario_teste,
            session=session,
            q=None,
            processo_recebimento='PROCESSO-INEXISTENTE',
            limit=25,
            offset=0,
        )
    )
    assert response_processo_inexistente['total'] == 0


def test_fila_exibe_nfse_quitada_e_permite_editar_e_excluir_recebimento(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='60.00')
    criar_nfse(
        session,
        row_hash='nfse-2',
        valor='40.00',
        numero_nfse='67890',
    )
    configurar_cards_oracle_fake(monkeypatch)
    for row_hash, valor in (('nfse-1', '60.00'), ('nfse-2', '40.00')):
        financeiro.conciliar_remessa_com_nfses(
            cd_remessa=CD_REMESSA_TESTE,
            payload=ConciliacaoRemessaCreate(
                processo_recebimento='PROC-HISTORICO',
                notas=[
                    {
                        'nfse_row_hash': row_hash,
                        'valor_alocado': valor,
                        'data_previsao_recebimento': '2026-08-10',
                    }
                ],
            ),
            usuario_atual=usuario_teste,
            session_postgres=session,
            session_oracle=object(),
        )
    conciliacao = session.scalar(
        select(ConciliacaoFaturamento).where(
            ConciliacaoFaturamento.numero_nfse == '12345'
        )
    )
    lancamento = LancamentoExtratoBancario(
        conta_bancaria_id=CONTA_BANCARIA_TESTE,
        data_lancamento=date(2026, 7, 10),
        valor=Decimal('60.00'),
        descricao='Crédito da NFS-e 12345',
    )
    lancamento.data_criacao = datetime(2026, 7, 10, 9, 0)
    session.add(lancamento)
    session.commit()
    criado = financeiro.registrar_recebimento_remessa(
        payload=RecebimentoRemessaCreate(
            conciliacao_id=conciliacao.id,
            cd_remessa=CD_REMESSA_TESTE,
            numero_nfse='12345',
            data_recebimento='2026-07-10',
            valor_recebido='60.00',
            conta_bancaria_id=CONTA_BANCARIA_TESTE,
            conta_plano_contas='1.1.1',
            conta_centro_custo='CC-10',
            lancamento_extrato_id=lancamento.id,
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=OracleComContaFake(),
    )

    fila = financeiro.consultar_conciliacoes_sem_recebimento(
        usuario_atual=usuario_teste,
        session=session,
        q=str(CD_REMESSA_TESTE),
        limit=25,
        offset=0,
    )
    card = fila['conciliacoes'][0]
    assert card['valor_recebido'] == Decimal('60.00')
    assert card['valor_pendente'] == Decimal('40.00')
    assert len(card['notas']) == CONCILIACOES_DISTRIBUIDAS
    nota_quitada = next(
        nota for nota in card['notas'] if nota['numero_nfse'] == '12345'
    )
    assert nota_quitada['situacao'] == 'recebido'
    assert nota_quitada['valor_recebido'] == Decimal('60.00')
    assert nota_quitada['valor_pendente'] == Decimal('0.00')
    assert nota_quitada['recebimentos'][0]['lancamento_extrato'][
        'descricao'
    ] == 'Crédito da NFS-e 12345'

    atualizado = financeiro.editar_recebimento_remessa(
        recebimento_id=criado['id'],
        payload=RecebimentoRemessaUpdate(
            data_recebimento='2026-07-10',
            valor_recebido='50.00',
            conta_bancaria_id=CONTA_BANCARIA_TESTE,
            conta_plano_contas='1.1.2',
            conta_centro_custo='CC-11',
            lancamento_extrato_id=lancamento.id,
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=OracleComContaFake(),
    )
    assert atualizado['valor_recebido'] == Decimal('50.00')
    fila_editada = financeiro.consultar_conciliacoes_sem_recebimento(
        usuario_atual=usuario_teste,
        session=session,
        q=str(CD_REMESSA_TESTE),
        limit=25,
        offset=0,
    )
    card_editado = fila_editada['conciliacoes'][0]
    nota_editada = next(
        nota
        for nota in card_editado['notas']
        if nota['numero_nfse'] == '12345'
    )
    assert card_editado['valor_recebido'] == Decimal('50.00')
    assert card_editado['valor_pendente'] == Decimal('50.00')
    assert nota_editada['situacao'] == 'recebimento_parcial'
    assert nota_editada['valor_pendente'] == Decimal('10.00')
    assert nota_editada['recebimentos'][0]['conta_plano_contas'] == '1.1.2'

    excluido = financeiro.excluir_recebimento_remessa(
        recebimento_id=criado['id'],
        usuario_atual=usuario_teste,
        session=session,
    )
    assert excluido['valor_total_recebido'] == Decimal('0.00')
    fila_reaberta = financeiro.consultar_conciliacoes_sem_recebimento(
        usuario_atual=usuario_teste,
        session=session,
        q=str(CD_REMESSA_TESTE),
        limit=25,
        offset=0,
    )
    card_reaberto = fila_reaberta['conciliacoes'][0]
    nota_reaberta = next(
        nota
        for nota in card_reaberto['notas']
        if nota['numero_nfse'] == '12345'
    )
    assert card_reaberto['valor_recebido'] == Decimal('0.00')
    assert card_reaberto['valor_pendente'] == Decimal('100.00')
    assert nota_reaberta['recebimentos'] == []
    assert nota_reaberta['situacao'] == 'sem_recebimento'
    assert (
        session.get(LancamentoExtratoBancario, lancamento.id).conciliado
        is False
    )
    auditorias = list(
        session.scalars(
            select(AuditoriaConciliacaoFaturamento)
            .where(
                AuditoriaConciliacaoFaturamento.conciliacao_id
                == conciliacao.id,
                AuditoriaConciliacaoFaturamento.acao.in_(
                    ('edicao_recebimento', 'exclusao_recebimento')
                ),
            )
            .order_by(AuditoriaConciliacaoFaturamento.id)
        )
    )
    assert [auditoria.acao for auditoria in auditorias] == [
        'edicao_recebimento',
        'exclusao_recebimento',
    ]
    assert auditorias[-1].usuario_id == usuario_teste.id
    assert auditorias[-1].dados_anteriores['recebimento']['id'] == criado['id']


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
    assert item['cd_remessa'] == cd_remessa_pendente
    assert item['situacao'] == 'sem_recebimento'
    assert item['quantidade_nfses_sem_recebimento'] == 1
    assert item['valor_recebido'] == Decimal('0.00')
    assert item['valor_pendente'] == Decimal('40.00')
    assert item['notas'][0]['numero_nfse'] == '12345'
    assert item['notas'][0]['situacao'] == 'sem_recebimento'

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


def test_marcacao_de_glosa_e_inferida_pelo_valor_glosado():
    com_glosa = ConciliacaoFaturamentoCreate(
        **payload_conciliacao(
            remessas=[
                {
                    'cd_remessa': CD_REMESSA_TESTE,
                    'sn_glosado': False,
                    'valor_glosado': '20.00',
                }
            ]
        )
    )
    sem_glosa = ConciliacaoFaturamentoCreate(
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

    assert com_glosa.remessas[0].sn_glosado is True
    assert sem_glosa.remessas[0].sn_glosado is False


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
    assert 'vl_total_registro' in sql
    assert 'vl_total_conta' not in sql


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


def test_lista_remessa_com_saldo_e_historico_centrados_no_faturamento(
    session,
    usuario_teste,
    monkeypatch,
):
    configurar_cards_oracle_fake(monkeypatch)
    monkeypatch.setattr(
        financeiro,
        'sincronizar_totais_remessas_financeiras',
        lambda *_args, **_kwargs: pytest.fail(
            'A listagem não deve sincronizar todas as remessas.'
        ),
    )

    response = financeiro.consultar_remessas_faturamento(
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
        q=None,
        limit=100,
        offset=0,
    )

    assert response['total'] == 1
    assert response['valor_total_conciliado'] == Decimal('0.00')
    assert response['valor_total_nao_conciliado'] == Decimal('120.00')
    assert response['remessas'][0] == {
        'cd_remessa': CD_REMESSA_TESTE,
        'data_competencia': date(2026, 7, 1),
        'convenio': 'Convenio Teste',
        'cnpj_convenio': '98765432000110',
        'valor_remessa': Decimal('120.00'),
        'valor_conciliado': Decimal('0.00'),
        'valor_impostos': Decimal('0.00'),
        'valor_acatado': Decimal('0.00'),
        'valor_nao_conciliado': Decimal('120.00'),
        'valor_recurso_disponivel': Decimal('0.00'),
        'valor_disponivel_conciliacao': Decimal('120.00'),
        'processo_recebimento': None,
        'historico': [],
    }


def test_lista_remessas_encaminha_filtros_separados(
    session,
    usuario_teste,
    monkeypatch,
):
    filtros_recebidos = {}

    def codigos_por_nfse(_session, numero_nfse):
        filtros_recebidos['numero_nfse'] = numero_nfse
        return {CD_REMESSA_TESTE}

    def consultar_cards(*_args, **kwargs):
        filtros_recebidos.update(kwargs)
        return [], 0

    monkeypatch.setattr(
        financeiro,
        '_codigos_remessas_por_nfse',
        codigos_por_nfse,
    )
    monkeypatch.setattr(
        financeiro,
        '_consultar_cards_remessas_hpc',
        consultar_cards,
    )

    response = financeiro.consultar_remessas_faturamento(
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
        q=None,
        numero_nfse='5032',
        cd_remessa='10521',
        convenio='BRADESCO',
        limit=25,
        offset=0,
    )

    assert response['remessas'] == []
    assert filtros_recebidos == {
        'numero_nfse': '5032',
        'q': None,
        'numero_remessa': '10521',
        'convenio': 'BRADESCO',
        'cd_remessas_nfse': {CD_REMESSA_TESTE},
        'limit': 25,
        'offset': 0,
    }


def test_saldo_da_remessa_trata_glosa_como_parte_da_pendencia():
    remessa = {
        'cd_remessa': 10386,
        'data_competencia': date(2024, 11, 1),
        'convenio': 'BRADESCO',
        'cnpj_convenio': '00000000000000',
        'valor_total': Decimal('2349.21'),
    }
    resumo = {
        'valor_conciliado': Decimal('2275.41'),
        'valor_glosado': Decimal('36.90'),
        'valor_recurso_consumido': Decimal('0.00'),
        'historico': [],
    }

    sem_recurso = financeiro._posicao_remessa(
        remessa,
        resumo,
        valor_acatado=Decimal('0.00'),
        recurso_disponivel=Decimal('0.00'),
    )
    com_recurso = financeiro._posicao_remessa(
        remessa,
        resumo,
        valor_acatado=Decimal('0.00'),
        recurso_disponivel=Decimal('20.00'),
    )

    assert sem_recurso['valor_nao_conciliado'] == Decimal('36.90')
    assert sem_recurso['valor_disponivel_conciliacao'] == Decimal('0.00')
    assert com_recurso['valor_nao_conciliado'] == Decimal('36.90')
    assert com_recurso['valor_disponivel_conciliacao'] == Decimal('20.00')


def test_posicao_da_remessa_concilia_liquido_e_impostos_e_preserva_glosa():
    posicao = financeiro._posicao_remessa(
        {
            'cd_remessa': CD_REMESSA_TESTE,
            'data_competencia': date(2026, 7, 1),
            'convenio': 'Convenio Teste',
            'cnpj_convenio': '98765432000110',
            'valor_total': Decimal('120.00'),
        },
        {
            'valor_conciliado': Decimal('90.00'),
            'valor_impostos': Decimal('10.00'),
            'valor_glosado': Decimal('30.00'),
            'valor_recurso_consumido': Decimal('0.00'),
            'historico': [],
        },
        valor_acatado=Decimal('0.00'),
        recurso_disponivel=Decimal('0.00'),
    )

    assert posicao['valor_conciliado'] == Decimal('90.00')
    assert posicao['valor_impostos'] == Decimal('10.00')
    assert posicao['valor_nao_conciliado'] == Decimal('30.00')


def test_busca_nfse_exibe_valores_bruto_liquido_e_saldos_separados(
    session,
):
    criar_nfse(session, valor='100.00')
    session.connection().connection.create_function(
        'regexp_replace',
        4,
        lambda value, pattern, replacement, _flags: re.sub(
            pattern,
            replacement,
            value or '',
        ),
    )

    notas = financeiro._consultar_nfses_com_saldo_para_remessa(
        session,
        {
            'cd_remessa': CD_REMESSA_TESTE,
            'convenio': 'Convenio Teste',
            'cnpj_convenio': '98765432000110',
            'valor_total': Decimal('120.00'),
        },
        {},
        Decimal('120.00'),
        None,
        50,
    )

    assert len(notas) == 1
    assert notas[0]['valor_bruto_nfse'] == Decimal('128.00')
    assert notas[0]['valor_nfse'] == Decimal('100.00')
    assert notas[0]['saldo_nfse'] == Decimal('100.00')
    assert notas[0]['impostos'] == Decimal('28.00')
    assert notas[0]['saldo_impostos'] == Decimal('28.00')


def test_valor_liquido_legado_exclui_glosa_e_impostos():
    vinculo = SimpleNamespace(
        valor_alocado_nfse=Decimal('0.00'),
        valor_total=Decimal('120.00'),
        valor_glosado=Decimal('20.00'),
        valor_impostos=Decimal('5.00'),
    )

    assert financeiro._valor_alocado_vinculo(vinculo) == Decimal('95.00')
    assert financeiro._valor_conciliado_vinculo(vinculo) == Decimal(
        '100.00'
    )


def test_uma_nfse_pode_distribuir_saldo_entre_remessas_distintas(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='100.00')
    configurar_cards_oracle_fake(monkeypatch)
    payload_primeira = ConciliacaoRemessaCreate(
        processo_recebimento='PROC-REM-987',
        notas=[
            {
                'nfse_row_hash': 'nfse-1',
                'valor_alocado': '60.00',
                'valor_impostos': '6.00',
                'data_previsao_recebimento': '2026-08-10',
            }
        ],
    )

    primeira = financeiro.conciliar_remessa_com_nfses(
        cd_remessa=987,
        payload=payload_primeira,
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )
    impostos_apos_primeira = financeiro._valores_impostos_utilizados_nfse(
        session
    )
    segunda = financeiro.conciliar_remessa_com_nfses(
        cd_remessa=988,
        payload=ConciliacaoRemessaCreate(
            processo_recebimento='PROC-REM-988',
            notas=[
                {
                    'nfse_row_hash': 'nfse-1',
                    'valor_alocado': '40.00',
                    'valor_impostos': '4.00',
                    'data_previsao_recebimento': '2026-08-11',
                }
            ],
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )

    assert primeira['valor_alocado'] == Decimal('60.00')
    assert primeira['valor_impostos'] == Decimal('6.00')
    assert segunda['valor_alocado'] == Decimal('40.00')
    assert segunda['valor_impostos'] == Decimal('4.00')
    assert primeira['remessa']['cd_remessa'] == CD_REMESSA_TESTE
    assert primeira['remessa']['valor_nao_conciliado'] == Decimal('54.00')
    assert impostos_apos_primeira == {
        ('12345', '98765432000110'): Decimal('6.00')
    }
    ConciliacaoRemessaPublic.model_validate(primeira)
    assert (
        session.query(ConciliacaoFaturamento).count()
        == CONCILIACOES_DISTRIBUIDAS
    )
    assert (
        session.query(ProcessoConciliacaoRemessa).count()
        == CONCILIACOES_DISTRIBUIDAS
    )
    assert sorted(
        session.scalars(
            select(ConciliacaoFaturamentoRemessa.valor_alocado_nfse)
        )
    ) == [Decimal('40.00'), Decimal('60.00')]
    assert sorted(
        session.scalars(
            select(ConciliacaoFaturamentoRemessa.valor_impostos)
        )
    ) == [Decimal('4.00'), Decimal('6.00')]


def test_glosa_mantem_remessa_aberta_e_exige_recurso_para_nova_nfse(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='80.00')
    configurar_cards_oracle_fake(monkeypatch)
    monkeypatch.setattr(
        financeiro,
        '_consultar_itens_remessas_hpc',
        itens_remessas_hpc,
    )

    conciliacao = financeiro.conciliar_remessa_com_nfses(
        cd_remessa=CD_REMESSA_TESTE,
        payload=ConciliacaoRemessaCreate(
            processo_recebimento='PROC-GLOSA-987',
            notas=[
                {
                    'nfse_row_hash': 'nfse-1',
                    'valor_alocado': '80.00',
                    'valor_glosado': '40.00',
                    'data_previsao_recebimento': '2026-08-10',
                }
            ],
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )
    response = financeiro.consultar_nfses_para_remessa(
        cd_remessa=CD_REMESSA_TESTE,
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
        q=None,
        limit=50,
    )

    assert response['notas'] == []
    assert response['valor_disponivel_remessa'] == Decimal('0.00')
    assert conciliacao['valor_nao_conciliado'] == Decimal('40.00')
    assert 'glosa ainda sem recurso' in response['message']
    assert session.query(RegistroGlosa).count() == ITENS_ANALITICOS_TESTE
    assert (
        session.scalar(
            select(ConciliacaoFaturamentoRemessa.sn_glosado)
        )
        == 'true'
    )


def test_conciliacao_normaliza_centavo_excedente_na_glosa(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='100.00')
    configurar_cards_oracle_fake(monkeypatch)
    monkeypatch.setattr(
        financeiro,
        '_consultar_itens_remessas_hpc',
        itens_remessas_hpc,
    )

    response = financeiro.conciliar_remessa_com_nfses(
        cd_remessa=CD_REMESSA_TESTE,
        payload=ConciliacaoRemessaCreate(
            processo_recebimento='PROC-ARREDONDAMENTO',
            notas=[
                {
                    'nfse_row_hash': 'nfse-1',
                    'valor_alocado': '80.00',
                    'valor_impostos': '10.00',
                    'valor_glosado': '30.01',
                    'data_previsao_recebimento': '2026-08-10',
                }
            ],
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )

    vinculo = session.scalar(select(ConciliacaoFaturamentoRemessa))
    assert response['valor_alocado'] == Decimal('80.00')
    assert response['valor_impostos'] == Decimal('10.00')
    assert response['valor_glosado'] == Decimal('30.00')
    assert vinculo.valor_glosado == Decimal('30.00')
    assert vinculo.valor_total == Decimal('120.00')


def test_edita_e_inativa_conciliacao_sem_recebimento_com_auditoria(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='100.00')
    configurar_cards_oracle_fake(monkeypatch)
    monkeypatch.setattr(
        financeiro,
        '_consultar_itens_remessas_hpc',
        itens_remessas_hpc,
    )
    financeiro.conciliar_remessa_com_nfses(
        cd_remessa=CD_REMESSA_TESTE,
        payload=ConciliacaoRemessaCreate(
            processo_recebimento='PROC-ORIGINAL',
            notas=[
                {
                    'nfse_row_hash': 'nfse-1',
                    'valor_alocado': '100.00',
                    'data_previsao_recebimento': '2026-08-10',
                }
            ],
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )
    conciliacao = session.scalar(select(ConciliacaoFaturamento))

    editada = financeiro.editar_conciliacao_faturamento(
        conciliacao_id=conciliacao.id,
        payload=ConciliacaoFaturamentoUpdate(
            processo_recebimento='PROC-CORRIGIDO',
            data_previsao_recebimento=date(2026, 8, 20),
            remessas=[
                {
                    'cd_remessa': CD_REMESSA_TESTE,
                    'valor_recebido': '90.00',
                    'valor_glosado': '10.00',
                    'valor_impostos': '10.00',
                }
            ],
        ),
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
    )

    assert editada['processo_recebimento'] == 'PROC-CORRIGIDO'
    assert editada['usuario_operacao_id'] == usuario_teste.id
    assert conciliacao.usuario_atualizacao_id == usuario_teste.id
    assert conciliacao.data_previsao_recebimento == date(2026, 8, 20)
    processo = session.scalar(select(ProcessoConciliacaoRemessa))
    assert processo.processo_recebimento == 'PROC-CORRIGIDO'
    assert processo.usuario_atualizacao_id == usuario_teste.id
    vinculo = session.scalar(select(ConciliacaoFaturamentoRemessa))
    assert vinculo.valor_alocado_nfse == Decimal('90.00')
    assert vinculo.valor_impostos == Decimal('10.00')
    assert vinculo.valor_glosado == Decimal('10.00')
    assert vinculo.valor_total == Decimal('110.00')
    assert vinculo.sn_glosado == 'true'
    assert session.query(RegistroGlosa).count() == ITENS_ANALITICOS_TESTE
    assert {
        registro.processo_controle_fatura_gab
        for registro in session.scalars(select(RegistroGlosa))
    } == {'PROC-CORRIGIDO'}

    inativada = financeiro.inativar_conciliacao_faturamento(
        conciliacao_id=conciliacao.id,
        usuario_atual=usuario_teste,
        session=session,
    )

    assert inativada['ativo'] is False
    assert conciliacao.usuario_inativacao_id == usuario_teste.id
    assert [
        auditoria.acao
        for auditoria in session.scalars(
            select(AuditoriaConciliacaoFaturamento).order_by(
                AuditoriaConciliacaoFaturamento.id
            )
        )
    ] == ['criacao', 'edicao', 'inativacao']
    assert financeiro._valores_utilizados_nfse(session) == {}
    assert financeiro._resumos_remessas(
        session,
        {CD_REMESSA_TESTE},
    ) == {}


def test_edicao_de_valores_respeita_saldo_da_nfse(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='100.00')
    configurar_cards_oracle_fake(monkeypatch)
    financeiro.conciliar_remessa_com_nfses(
        cd_remessa=CD_REMESSA_TESTE,
        payload=ConciliacaoRemessaCreate(
            processo_recebimento='PROC-ORIGINAL',
            notas=[
                {
                    'nfse_row_hash': 'nfse-1',
                    'valor_alocado': '100.00',
                    'data_previsao_recebimento': '2026-08-10',
                }
            ],
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )
    conciliacao = session.scalar(select(ConciliacaoFaturamento))

    with pytest.raises(HTTPException) as error:
        financeiro.editar_conciliacao_faturamento(
            conciliacao_id=conciliacao.id,
            payload=ConciliacaoFaturamentoUpdate(
                remessas=[
                    {
                        'cd_remessa': CD_REMESSA_TESTE,
                        'valor_recebido': '101.00',
                        'valor_glosado': '0.00',
                    }
                ]
            ),
            usuario_atual=usuario_teste,
            session=session,
            session_oracle=object(),
        )

    assert error.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert 'saldo disponivel da NFS-e' in error.value.detail


def test_edicao_de_impostos_respeita_saldo_fiscal_da_nfse(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='100.00')
    configurar_cards_oracle_fake(monkeypatch)
    financeiro.conciliar_remessa_com_nfses(
        cd_remessa=CD_REMESSA_TESTE,
        payload=ConciliacaoRemessaCreate(
            processo_recebimento='PROC-IMPOSTOS',
            notas=[
                {
                    'nfse_row_hash': 'nfse-1',
                    'valor_alocado': '90.00',
                    'valor_impostos': '10.00',
                    'data_previsao_recebimento': '2026-08-10',
                }
            ],
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )
    conciliacao = session.scalar(select(ConciliacaoFaturamento))

    with pytest.raises(HTTPException) as error:
        financeiro.editar_conciliacao_faturamento(
            conciliacao_id=conciliacao.id,
            payload=ConciliacaoFaturamentoUpdate(
                remessas=[
                    {
                        'cd_remessa': CD_REMESSA_TESTE,
                        'valor_recebido': '89.00',
                        'valor_impostos': '29.00',
                        'valor_glosado': '0.00',
                    }
                ]
            ),
            usuario_atual=usuario_teste,
            session=session,
            session_oracle=object(),
        )

    assert error.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert 'saldo de retencoes da NFS-e' in error.value.detail


def test_edicao_do_valor_recebido_preserva_glosa_ja_tratada(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='80.00')
    configurar_cards_oracle_fake(monkeypatch)
    monkeypatch.setattr(
        financeiro,
        '_consultar_itens_remessas_hpc',
        itens_remessas_hpc,
    )
    financeiro.conciliar_remessa_com_nfses(
        cd_remessa=CD_REMESSA_TESTE,
        payload=ConciliacaoRemessaCreate(
            processo_recebimento='PROC-GLOSA',
            notas=[
                {
                    'nfse_row_hash': 'nfse-1',
                    'valor_alocado': '80.00',
                    'valor_glosado': '40.00',
                    'sn_glosado': True,
                    'data_previsao_recebimento': '2026-08-10',
                }
            ],
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )
    conciliacao = session.scalar(select(ConciliacaoFaturamento))
    registro = session.scalar(select(RegistroGlosa))
    registro.processo_recurso = 'REC-TRATADO'
    registro.dt_recurso = date(2026, 8, 12)
    registro.valor_recursado = Decimal('20.00')
    session.commit()

    financeiro.editar_conciliacao_faturamento(
        conciliacao_id=conciliacao.id,
        payload=ConciliacaoFaturamentoUpdate(
            remessas=[
                {
                    'cd_remessa': CD_REMESSA_TESTE,
                    'valor_recebido': '70.00',
                    'valor_glosado': '40.00',
                }
            ]
        ),
        usuario_atual=usuario_teste,
        session=session,
        session_oracle=object(),
    )

    session.refresh(registro)
    assert registro.sn_ativo == 'true'
    assert registro.sn_glosado == 'true'
    assert registro.processo_recurso == 'REC-TRATADO'
    assert registro.valor_recursado == Decimal('20.00')


def test_consulta_gerencial_exibe_conciliacao_recebimento_e_usuarios(
    session,
    usuario_teste,
):
    vinculo = criar_conciliacao_anterior_com_glosa(
        session,
        usuario_teste.id,
    )
    conciliacao = session.get(
        ConciliacaoFaturamento,
        vinculo.conciliacao_id,
    )
    financeiro._registrar_auditoria_conciliacao(
        session,
        conciliacao.id,
        'criacao',
        usuario_teste.id,
        dados_novos=financeiro._snapshot_conciliacao(
            conciliacao,
            [vinculo],
        ),
    )
    session.commit()

    response = financeiro.consultar_conciliacoes_faturamento(
        usuario_atual=usuario_teste,
        session=session,
        q=None,
        numero_nfse='NFSE-ANTERIOR',
        remessa_filtro=str(CD_REMESSA_TESTE),
        convenio='Convenio Teste',
        processo_recebimento='PROC-ANTERIOR',
        situacao='recebido',
        incluir_inativas=False,
        limit=25,
        offset=0,
    )

    assert response['total'] == 1
    card = response['conciliacoes'][0]
    nota = card['notas'][0]
    assert card['cd_remessa'] == CD_REMESSA_TESTE
    assert card['situacao_recebimento'] == 'recebido'
    assert nota['usuario_criacao']['id'] == usuario_teste.id
    assert nota['recebimentos'][0]['usuario']['id'] == usuario_teste.id
    assert card['auditoria'][0]['acao'] == 'criacao'
    assert card['auditoria'][0]['numero_nfse'] == conciliacao.numero_nfse

    sem_correspondencia = financeiro.consultar_conciliacoes_faturamento(
        usuario_atual=usuario_teste,
        session=session,
        q=None,
        numero_nfse='NFSE-ANTERIOR',
        remessa_filtro=str(CD_REMESSA_TESTE),
        convenio='Convenio Teste',
        processo_recebimento='PROCESSO-INEXISTENTE',
        situacao='recebido',
        incluir_inativas=False,
        limit=25,
        offset=0,
    )
    assert sem_correspondencia['total'] == 0


def test_consulta_gerencial_reune_historico_de_conciliacao_recriada(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='100.00')
    configurar_cards_oracle_fake(monkeypatch)
    financeiro.conciliar_remessa_com_nfses(
        cd_remessa=CD_REMESSA_TESTE,
        payload=ConciliacaoRemessaCreate(
            processo_recebimento='PROC-LINHA-DO-TEMPO',
            notas=[
                {
                    'nfse_row_hash': 'nfse-1',
                    'valor_alocado': '60.00',
                    'data_previsao_recebimento': '2026-08-10',
                }
            ],
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )
    conciliacao_anterior = session.scalar(select(ConciliacaoFaturamento))
    financeiro.inativar_conciliacao_faturamento(
        conciliacao_id=conciliacao_anterior.id,
        usuario_atual=usuario_teste,
        session=session,
    )
    financeiro.conciliar_remessa_com_nfses(
        cd_remessa=CD_REMESSA_TESTE,
        payload=ConciliacaoRemessaCreate(
            processo_recebimento='PROC-LINHA-DO-TEMPO',
            notas=[
                {
                    'nfse_row_hash': 'nfse-1',
                    'valor_alocado': '50.00',
                    'data_previsao_recebimento': '2026-08-20',
                }
            ],
        ),
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=object(),
    )

    response = financeiro.consultar_conciliacoes_faturamento(
        usuario_atual=usuario_teste,
        session=session,
        q=None,
        remessa_filtro=str(CD_REMESSA_TESTE),
        situacao=None,
        incluir_inativas=False,
        limit=25,
        offset=0,
    )

    assert response['total'] == 1
    card = response['conciliacoes'][0]
    assert card['cd_remessa'] == CD_REMESSA_TESTE
    assert card['notas'][0]['id'] != conciliacao_anterior.id
    assert [evento['acao'] for evento in card['auditoria']] == [
        'criacao',
        'inativacao',
        'criacao',
    ]
    assert {
        evento['conciliacao_origem_id'] for evento in card['auditoria']
    } == {conciliacao_anterior.id, card['notas'][0]['id']}
    assert {evento['numero_nfse'] for evento in card['auditoria']} == {
        conciliacao_anterior.numero_nfse
    }


def test_consulta_gerencial_agrupa_notas_por_remessa(
    session,
    usuario_teste,
    monkeypatch,
):
    criar_nfse(session, valor='100.00')
    criar_nfse(
        session,
        row_hash='nfse-2',
        valor='100.00',
        numero_nfse='67890',
    )
    configurar_cards_oracle_fake(monkeypatch)
    for row_hash, valor in (('nfse-1', '60.00'), ('nfse-2', '50.00')):
        financeiro.conciliar_remessa_com_nfses(
            cd_remessa=CD_REMESSA_TESTE,
            payload=ConciliacaoRemessaCreate(
                processo_recebimento='PROC-AGRUPADO',
                notas=[
                    {
                        'nfse_row_hash': row_hash,
                        'valor_alocado': valor,
                        'data_previsao_recebimento': '2026-08-10',
                    }
                ],
            ),
            usuario_atual=usuario_teste,
            session_postgres=session,
            session_oracle=object(),
        )

    response = financeiro.consultar_conciliacoes_faturamento(
        usuario_atual=usuario_teste,
        session=session,
        q=None,
        remessa_filtro=str(CD_REMESSA_TESTE),
        situacao=None,
        incluir_inativas=False,
        limit=25,
        offset=0,
    )

    assert response['total'] == 1
    assert response['total_ativas'] == 1
    assert response['total_sem_recebimento'] == 1
    card = response['conciliacoes'][0]
    assert card['cd_remessa'] == CD_REMESSA_TESTE
    assert {nota['numero_nfse'] for nota in card['notas']} == {
        '12345',
        '67890',
    }
    assert {evento['numero_nfse'] for evento in card['auditoria']} == {
        '12345',
        '67890',
    }
    ConciliacoesGerenciamentoList.model_validate(response)


def test_nao_inativa_conciliacao_com_recebimento(
    session,
    usuario_teste,
):
    vinculo = criar_conciliacao_anterior_com_glosa(
        session,
        usuario_teste.id,
    )

    with pytest.raises(HTTPException) as error:
        financeiro.inativar_conciliacao_faturamento(
            conciliacao_id=vinculo.conciliacao_id,
            usuario_atual=usuario_teste,
            session=session,
        )

    assert error.value.status_code == HTTPStatus.CONFLICT
    assert 'recebimento bancário' in error.value.detail


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
        processo_recurso=None,
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
    assert 'excede o saldo em aberto de R$ 0,00' in exc_info.value.detail


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


def test_recurso_sem_numero_de_processo_e_considerado_em_aberto(session):
    criar_recurso_aberto(session, processo_recurso=None)

    assert financeiro._recursos_abertos_por_remessa(
        session,
        {CD_REMESSA_TESTE},
    ) == {CD_REMESSA_TESTE: Decimal('20.00')}


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


def test_recebimento_posterior_permite_parcelas_ate_quitacao(  # noqa: PLR0915
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
    primeira_parcela = financeiro.registrar_recebimento_remessa(
        payload=payload_recebimento,
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=OracleComContaFake(),
    )
    assert primeira_parcela['recebimento_integral'] is False
    assert primeira_parcela['remessa_recebida_integralmente'] is False
    assert primeira_parcela['valor_total_recebido'] == Decimal('50.00')
    assert primeira_parcela['saldo_em_aberto'] == Decimal('70.00')
    conciliacao = session.scalar(select(ConciliacaoFaturamento))
    assert conciliacao.data_recebimento is None
    assert (
        session.get(LancamentoExtratoBancario, lancamento.id).conciliado
        is True
    )

    fila_parcial = financeiro.consultar_conciliacoes_sem_recebimento(
        usuario_atual=usuario_teste,
        session=session,
        q='12345',
        limit=100,
        offset=0,
    )
    assert fila_parcial['total'] == 1
    nota_pendente = fila_parcial['conciliacoes'][0]['notas'][0]
    assert nota_pendente['valor_recebido'] == Decimal('50.00')
    assert nota_pendente['valor_pendente'] == Decimal('70.00')
    assert nota_pendente['situacao'] == 'recebimento_parcial'
    assert len(nota_pendente['recebimentos']) == 1
    assert nota_pendente['recebimentos'][0] == {
        'id': nota_pendente['recebimentos'][0]['id'],
        'data_recebimento': date(2026, 7, 10),
        'valor_recebido': Decimal('50.00'),
        'saldo_financeiro': Decimal('70.00'),
        'conta_bancaria_id': 7,
        'conta_plano_contas': '1.1.1',
        'conta_centro_custo': 'CC-10',
        'lancamento_extrato_id': lancamento.id,
        'lancamento_extrato': {
            'id': lancamento.id,
            'conta_bancaria_id': 7,
            'data_lancamento': date(2026, 7, 10),
            'valor': Decimal('120.00'),
            'descricao': 'Recebimento NFS-e 12345',
            'documento': None,
        },
        'data_registro': nota_pendente['recebimentos'][0][
            'data_registro'
        ],
    }
    ConciliacoesSemRecebimentoList.model_validate(fila_parcial)

    segundo_lancamento = LancamentoExtratoBancario(
        conta_bancaria_id=CONTA_BANCARIA_TESTE,
        data_lancamento=date(2026, 7, 11),
        valor=Decimal('20.00'),
        descricao='Segunda parcela NFS-e 12345',
    )
    segundo_lancamento.data_criacao = datetime(2026, 7, 11, 9, 0)
    session.add(segundo_lancamento)
    session.commit()
    segunda_parcela_payload = payload_recebimento.model_copy(
        update={
            'data_recebimento': date(2026, 7, 11),
            'valor_recebido': Decimal('20.00'),
            'lancamento_extrato_id': segundo_lancamento.id,
        }
    )
    segunda_parcela = financeiro.registrar_recebimento_remessa(
        payload=segunda_parcela_payload,
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=OracleComContaFake(),
    )
    assert segunda_parcela['recebimento_integral'] is False
    assert segunda_parcela['remessa_recebida_integralmente'] is False
    assert segunda_parcela['valor_total_recebido'] == Decimal('70.00')
    assert segunda_parcela['saldo_em_aberto'] == Decimal('50.00')

    fila_ainda_parcial = financeiro.consultar_conciliacoes_sem_recebimento(
        usuario_atual=usuario_teste,
        session=session,
        q='12345',
        limit=100,
        offset=0,
    )
    assert fila_ainda_parcial['total'] == 1
    nota_ainda_pendente = fila_ainda_parcial['conciliacoes'][0]['notas'][0]
    assert nota_ainda_pendente['valor_recebido'] == Decimal('70.00')
    assert nota_ainda_pendente['valor_pendente'] == Decimal('50.00')
    assert nota_ainda_pendente['situacao'] == 'recebimento_parcial'
    assert (
        len(nota_ainda_pendente['recebimentos'])
        == PARCELAS_ANTES_QUITACAO
    )
    assert [
        (item['valor_recebido'], item['saldo_financeiro'])
        for item in nota_ainda_pendente['recebimentos']
    ] == [
        (Decimal('50.00'), Decimal('70.00')),
        (Decimal('20.00'), Decimal('50.00')),
    ]

    terceiro_lancamento = LancamentoExtratoBancario(
        conta_bancaria_id=CONTA_BANCARIA_TESTE,
        data_lancamento=date(2026, 7, 12),
        valor=Decimal('50.00'),
        descricao='Quitação NFS-e 12345',
    )
    terceiro_lancamento.data_criacao = datetime(2026, 7, 12, 9, 0)
    session.add(terceiro_lancamento)
    session.commit()
    terceira_parcela_payload = payload_recebimento.model_copy(
        update={
            'data_recebimento': date(2026, 7, 12),
            'valor_recebido': Decimal('50.00'),
            'lancamento_extrato_id': terceiro_lancamento.id,
        }
    )
    response = financeiro.registrar_recebimento_remessa(
        payload=terceira_parcela_payload,
        usuario_atual=usuario_teste,
        session_postgres=session,
        session_oracle=OracleComContaFake(),
    )
    assert response['recebimento_integral'] is True
    assert response['remessa_recebida_integralmente'] is True
    assert response['valor_total_recebido'] == Decimal('120.00')
    assert response['saldo_em_aberto'] == Decimal('0.00')
    recebimentos = list(
        session.scalars(
            select(RecebimentoRemessa).order_by(RecebimentoRemessa.id)
        )
    )
    assert [item.valor_recebido for item in recebimentos] == [
        Decimal('50.00'),
        Decimal('20.00'),
        Decimal('50.00'),
    ]
    assert recebimentos[0].conta_plano_contas == '1.1.1'
    assert recebimentos[0].conta_centro_custo == 'CC-10'
    assert recebimentos[0].lancamento_extrato_id == lancamento.id
    session.refresh(conciliacao)
    assert conciliacao.data_recebimento == date(2026, 7, 12)
    assert (
        session.get(
            LancamentoExtratoBancario,
            terceiro_lancamento.id,
        ).conciliado
        is True
    )

    fila_quitada = financeiro.consultar_conciliacoes_sem_recebimento(
        usuario_atual=usuario_teste,
        session=session,
        q='12345',
        limit=100,
        offset=0,
    )
    assert fila_quitada['total'] == 0

    with pytest.raises(HTTPException) as duplicate_info:
        financeiro.registrar_recebimento_remessa(
            payload=terceira_parcela_payload.model_copy(
                update={'lancamento_extrato_id': None}
            ),
            usuario_atual=usuario_teste,
            session_postgres=session,
            session_oracle=OracleComContaFake(),
        )
    assert duplicate_info.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert 'excede o saldo em aberto' in duplicate_info.value.detail
