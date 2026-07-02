from copy import deepcopy
from datetime import datetime
from http import HTTPStatus

from app_prontocardio.models import PrazoRecursoConvenio, RegistroGlosa
from app_prontocardio.routers.app_glosas import (
    consultar_convenios,
    consultar_glosas_registradas,
    salvar_prazos_recurso_convenio,
)
from app_prontocardio.schema import (
    FilterSearch,
    PrazoRecursoConvenioInput,
    RegistroGlosaCreate,
)


def test_conta_atendimento_exige_criterio(cliente, token_teste):
    response = cliente.get(
        '/app_glosas/',
        headers={'Authorization': f'Bearer {token_teste}'},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def registro_glosa_payload(**overrides):
    payload = {
        'codigo_paciente': 1,
        'cd_remessa': 1234,
        'cd_atendimento': 271445,
        'conta': 333709,
        'cd_prestador': 10,
        'cd_convenio': 20,
        'tp_atendimento': 'Ambulatório',
        'procedimento': 'CONSULTA EM CONSULTORIO',
        'convenio': 'CASSI',
        'guia': '123456',
        'prestador': 'JOSE MARTINS CORDEIRO',
        'data_atendimento': '2025-11-22T00:00:00',
        'valor': '103.45',
        'processo_controle_fatura_gab': 'xptou xptou',
        'processo_recurso': 'ugkgkg',
        'data_glosa': '2026-06-10',
        'motivo_glosa': '1008 - ASSINATURA DIVERGENTE',
        'descricao_glosa': 'descricao da glosa',
        'qtd_registro': '2',
        'qtd_glosada': '1',
        'valor_glosado': '12.31',
        'dt_recurso': '2026-06-16',
        'dt_pagamento': '2026-06-11',
        'sn_glosado': 'true',
    }
    payload.update(overrides)
    return payload


def test_criar_glosa_ignora_sn_ativo_do_payload(cliente, token_teste):
    payload = registro_glosa_payload(sn_ativo='not')

    response = cliente.post(
        '/app_glosas/glosas',
        headers={'Authorization': f'Bearer {token_teste}'},
        json=payload,
    )

    assert response.status_code == HTTPStatus.CREATED
    assert response.json()['sn_ativo'] == 'true'

    response = cliente.get(
        '/app_glosas/glosas',
        headers={'Authorization': f'Bearer {token_teste}'},
        params={'cd_reg': payload['conta']},
    )

    assert response.status_code == HTTPStatus.OK
    assert len(response.json()['glosas']) == 1
    assert response.json()['glosas'][0]['sn_ativo'] == 'true'


def test_filtra_glosas_de_convenio_desabilitado(session):
    payload = RegistroGlosaCreate(**registro_glosa_payload())
    registro = RegistroGlosa(
        **payload.model_dump(),
        sn_ativo='true',
    )
    prazo = PrazoRecursoConvenio(
        cd_convenio=20,
        convenio='CASSI',
        dias_para_recurso=10,
        habilitado=False,
    )
    registro.data_criacao = datetime.now()
    prazo.data_atualizacao = datetime.now()
    session.add_all([registro, prazo])
    session.commit()

    response = consultar_glosas_registradas(
        usuario_atual=None,
        campos_pesquisados=FilterSearch(cd_reg=333709),
        session=session,
        tp_atendimento=None,
        incluir_inativos=False,
    )
    assert response['glosas'] == []


def test_convenio_habilitado_por_padrao(session):
    prazo = PrazoRecursoConvenio(
        cd_convenio=20,
        convenio='CASSI',
        dias_para_recurso=5,
    )
    prazo.data_atualizacao = datetime.now()
    session.add(prazo)
    session.commit()

    response = salvar_prazos_recurso_convenio(
        payload=[
            PrazoRecursoConvenioInput(
                cd_convenio=20,
                convenio='CASSI',
                dias_para_recurso=10,
            )
        ],
        usuario_atual=None,
        session=session,
    )

    assert response['convenios'][0]['habilitado'] is True


def test_endpoint_convenios_retorna_apenas_habilitados(session):
    prazo = PrazoRecursoConvenio(
        cd_convenio=20,
        convenio='CASSI',
        dias_para_recurso=10,
        habilitado=False,
    )
    prazo.data_atualizacao = datetime.now()
    session.add(prazo)
    session.commit()

    class OracleResult:
        @staticmethod
        def all():
            return [(20, 'CASSI'), (21, 'UNIMED')]

    class OracleSession:
        @staticmethod
        def execute(_query):
            return OracleResult()

    response = consultar_convenios(
        usuario_atual=None,
        session_postgres=session,
        session_oracle=OracleSession(),
    )

    assert response == {
        'convenios': [{'cd_convenio': 21, 'nm_convenio': 'UNIMED'}]
    }


def test_rejeita_glosa_sem_dados_obrigatorios(cliente, token_teste):
    payload = registro_glosa_payload(
        processo_controle_fatura_gab='',
        processo_recurso='',
        motivo_glosa='',
        dt_pagamento=None,
        dt_recurso=None,
        qtd_glosada=None,
        valor_glosado=None,
    )

    response = cliente.post(
        '/app_glosas/glosas',
        headers={'Authorization': f'Bearer {token_teste}'},
        json=payload,
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_rejeita_datas_quantidade_e_valor_invalidos(cliente, token_teste):
    casos = (
        {'data_glosa': '2026-06-12', 'dt_pagamento': '2026-06-11'},
        {'dt_recurso': '2026-06-09'},
        {'data_glosa': '2999-01-01'},
        {'dt_pagamento': '2999-01-01'},
        {'dt_recurso': '2999-01-01'},
        {'qtd_glosada': '3'},
        {'valor_glosado': '103.46'},
    )

    for override in casos:
        response = cliente.post(
            '/app_glosas/glosas',
            headers={'Authorization': f'Bearer {token_teste}'},
            json=registro_glosa_payload(**override),
        )
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_registra_recebimento_com_qtd_recebida(cliente, token_teste):
    response = cliente.post(
        '/app_glosas/glosas',
        headers={'Authorization': f'Bearer {token_teste}'},
        json=registro_glosa_payload(qtd_glosada='2', valor_glosado='12.31'),
    )
    assert response.status_code == HTTPStatus.CREATED

    response = cliente.patch(
        f'/app_glosas/glosas/{response.json()["id"]}/recebimento',
        headers={'Authorization': f'Bearer {token_teste}'},
        json={
            'dt_recebimento': '2026-06-20',
            'valor_recebido': '12.31',
            'qtd_recebida': '2',
            'observacao_recebimento': 'Recebido integralmente',
        },
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()['dt_recebimento'] == '2026-06-20'
    assert response.json()['valor_recebido'] == '12.31'
    assert response.json()['qtd_recebida'] == '2.00'


def test_rejeita_recebimento_em_acato(cliente, token_teste):
    response = cliente.post(
        '/app_glosas/glosas',
        headers={'Authorization': f'Bearer {token_teste}'},
        json=registro_glosa_payload(sn_glosado='not'),
    )
    assert response.status_code == HTTPStatus.CREATED

    response = cliente.patch(
        f'/app_glosas/glosas/{response.json()["id"]}/recebimento',
        headers={'Authorization': f'Bearer {token_teste}'},
        json={
            'dt_recebimento': '2026-06-20',
            'valor_recebido': '10.00',
            'qtd_recebida': '1',
        },
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_rejeita_valor_recebido_zero(cliente, token_teste):
    response = cliente.post(
        '/app_glosas/glosas',
        headers={'Authorization': f'Bearer {token_teste}'},
        json=registro_glosa_payload(),
    )
    assert response.status_code == HTTPStatus.CREATED

    response = cliente.patch(
        f'/app_glosas/glosas/{response.json()["id"]}/recebimento',
        headers={'Authorization': f'Bearer {token_teste}'},
        json={
            'dt_recebimento': '2026-06-20',
            'valor_recebido': '0',
            'qtd_recebida': '1',
        },
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_rejeita_qtd_recebida_zero(cliente, token_teste):
    response = cliente.post(
        '/app_glosas/glosas',
        headers={'Authorization': f'Bearer {token_teste}'},
        json=registro_glosa_payload(),
    )
    assert response.status_code == HTTPStatus.CREATED

    response = cliente.patch(
        f'/app_glosas/glosas/{response.json()["id"]}/recebimento',
        headers={'Authorization': f'Bearer {token_teste}'},
        json={
            'dt_recebimento': '2026-06-20',
            'valor_recebido': '10.00',
            'qtd_recebida': '0',
        },
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_rejeita_recebimento_maior_que_qtd_recursada(cliente, token_teste):
    response = cliente.post(
        '/app_glosas/glosas',
        headers={'Authorization': f'Bearer {token_teste}'},
        json=registro_glosa_payload(qtd_glosada='1'),
    )
    assert response.status_code == HTTPStatus.CREATED

    response = cliente.patch(
        f'/app_glosas/glosas/{response.json()["id"]}/recebimento',
        headers={'Authorization': f'Bearer {token_teste}'},
        json={
            'dt_recebimento': '2026-06-20',
            'valor_recebido': '10.00',
            'qtd_recebida': '2',
        },
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert 'quantidade recursada' in response.json()['detail'].lower()


def test_rejeita_recebimento_maior_que_valor_recursado(cliente, token_teste):
    response = cliente.post(
        '/app_glosas/glosas',
        headers={'Authorization': f'Bearer {token_teste}'},
        json=registro_glosa_payload(valor_glosado='12.31'),
    )
    assert response.status_code == HTTPStatus.CREATED

    response = cliente.patch(
        f'/app_glosas/glosas/{response.json()["id"]}/recebimento',
        headers={'Authorization': f'Bearer {token_teste}'},
        json={
            'dt_recebimento': '2026-06-20',
            'valor_recebido': '12.32',
            'qtd_recebida': '1',
        },
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert 'valor recursado' in response.json()['detail'].lower()


def test_rejeita_acato_criado_com_recebimento(cliente, token_teste):
    response = cliente.post(
        '/app_glosas/glosas',
        headers={'Authorization': f'Bearer {token_teste}'},
        json=registro_glosa_payload(
            sn_glosado='not',
            dt_recebimento='2026-06-20',
            valor_recebido='10.00',
            qtd_recebida='1',
        ),
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_atualizar_glosa_reativa_estado_sujo_do_payload(cliente, token_teste):
    payload = registro_glosa_payload()
    response = cliente.post(
        '/app_glosas/glosas',
        headers={'Authorization': f'Bearer {token_teste}'},
        json=payload,
    )
    glosa_id = response.json()['id']

    update_payload = deepcopy(payload)
    update_payload['descricao_glosa'] = 'descricao atualizada'
    update_payload['sn_ativo'] = 'not'
    response = cliente.put(
        f'/app_glosas/glosas/{glosa_id}',
        headers={'Authorization': f'Bearer {token_teste}'},
        json=update_payload,
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()['descricao_glosa'] == 'descricao atualizada'
    assert response.json()['sn_ativo'] == 'true'
