from http import HTTPStatus
from types import SimpleNamespace

from app_prontocardio.database import get_session_oracle
from app_prontocardio.routers import app_glosas, financeiro
from app_prontocardio.schema import RegistroGlosaCreate
from app_prontocardio.services.pdf_recurso_glosa import (
    preencher_processo_recurso_issec,
)
from tests.routers.test_app_glosas import registro_glosa_payload


def _registro(session, usuario):
    return app_glosas.registrar_glosa(
        RegistroGlosaCreate(
            **registro_glosa_payload(
                convenio='ISSEC',
                nm_paciente='Paciente de teste',
                processo_controle_fatura_gab='2600027503',
                motivo_glosa='1008',
                cd_lancamento=1,
            )
        ),
        usuario,
        session,
    )


def _card():
    return {
        'cd_remessa': 1234,
        'convenio': 'ISSEC',
        'numero_nfse': '',
        'valor_remessa': '103.45',
        'valor_itens': '103.45',
        'valor_glosado': '103.45',
        'valor_glosa_pendente': '0',
        'valor_total_tratado': '103.45',
        'possui_recurso': True,
        'processo': {'numero_processo': '2600027503'},
        'fiscal': {
            'numero_nfse': '',
            'valor_servicos': '0',
            'impostos': '0',
            'valor_liquido_nfse': '0',
        },
        'pacientes': [],
    }


def test_segunda_pagina_serializa_registro_com_relacionamentos(
    cliente, session, usuario_teste, token_teste, monkeypatch
):
    registro = _registro(session, usuario_teste)
    # O modelo ORM tem relacionamentos que não devem entrar no JSON público.
    registro.usuario = usuario_teste
    paciente = financeiro._pacientes_follow_up_glosa([registro], {}, {})
    cards = []
    for indice in range(11):
        card = _card()
        card['processo']['numero_processo'] = str(indice)
        card['pacientes'] = paciente
        cards.append(card)
    monkeypatch.setattr(
        financeiro,
        'consultar_follow_up_glosas',
        lambda **_kwargs: {'cards': cards, 'total': 11},
    )
    cliente.app.dependency_overrides[get_session_oracle] = lambda: None
    response = cliente.get(
        '/app_glosas/financeiro/conciliacao-faturamento/recursos-processos',
        params={'limit': 10, 'offset': 10},
        headers={'Authorization': f'Bearer {token_teste}'},
    )
    assert response.status_code == HTTPStatus.OK
    resultado = response.json()
    assert resultado['total'] == len(cards)
    item = resultado['processos'][0]['cards'][0]['pacientes'][0]['itens'][0]
    assert item['registro_recusa']['id'] == registro.id
    assert 'usuario' not in item['registro_recusa']


def test_detalhamento_recupera_triagem_sem_sobrescrever_demonstrativo(
    session, usuario_teste
):
    registro = _registro(session, usuario_teste)
    card = _card()
    financeiro._completar_itens_recursos_triagem(
        session, [card], ' 2600027503 ', None
    )
    item = card['pacientes'][0]['itens'][0]
    assert item['registro_recusa'].id == registro.id
    original = item['descricao']
    financeiro._completar_itens_recursos_triagem(
        session, [card], '2600027503', None
    )
    assert card['pacientes'][0]['itens'][0]['descricao'] == (original)


def test_pdf_issec_consulta_cadastro_e_deixa_vazio_sem_cadastro():
    class Sessao:
        def scalars(self, _consulta):
            return [
                SimpleNamespace(
                    processo_original_normalizado='2600027503',
                    processo_recurso='XPTO',
                )
            ]

    card = _card()
    preencher_processo_recurso_issec(Sessao(), [card])
    assert card['processo_recurso'] == 'XPTO'
    card['processo']['numero_processo'] = 'outro'
    preencher_processo_recurso_issec(Sessao(), [card])
    assert card['processo_recurso'] == ''
