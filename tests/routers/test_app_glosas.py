from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from http import HTTPStatus

import pytest
from fastapi import HTTPException

from app_prontocardio.models import (
    ModelContaAtendimento,
    PrazoRecursoConvenio,
    RegistroGlosa,
)
from app_prontocardio.routers.app_glosas import (
    consultar_convenios,
    consultar_glosas_registradas,
    deletar_glosa,
    editar_glosa,
    registrar_glosa,
    registrar_recebimento_glosa,
    salvar_prazos_recurso_convenio,
)
from app_prontocardio.schema import (
    Atendimento,
    FilterSearch,
    PrazoRecursoConvenioInput,
    RegistroGlosaCreate,
    RegistroGlosaRecebimentoUpdate,
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


def test_atendimento_aceita_identificadores_alfanumericos_da_view():
    atendimento = Atendimento(
        cd_reg=1,
        cd_lancamento=2,
        cd_pro_fat='U370796',
        nr_guia='GUIA-ABC',
        nr_carteira='CARTEIRA-123',
        cd_ati_med='AT-01',
        cnpj_convenio='39427632000171',
    )

    assert atendimento.cd_pro_fat == 'U370796'
    assert atendimento.nr_guia == 'GUIA-ABC'
    assert atendimento.nr_carteira == 'CARTEIRA-123'
    assert atendimento.cd_ati_med == 'AT-01'


def test_modelo_conta_atendimento_mapeia_nr_carteira_da_view():
    coluna = ModelContaAtendimento.__table__.c.nr_carteira

    assert str(coluna.type) == 'VARCHAR(25)'
    assert coluna.nullable is True


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


def test_registro_triagem_preserva_contrato_dos_indicadores(
    session,
    usuario_teste,
):
    registro = registrar_glosa(
        RegistroGlosaCreate(**registro_glosa_payload()),
        usuario_teste,
        session,
    )

    assert registro.origem_registro == 'triagem'
    assert registro.status_tratativa == 'recurso'
    assert registro.valor_indicador == Decimal('12.31')


def test_desfazer_registro_independente_mantem_exclusao_logica(
    session,
    usuario_teste,
):
    registro = registrar_glosa(
        RegistroGlosaCreate(**registro_glosa_payload()),
        usuario_teste,
        session,
    )

    response = deletar_glosa(registro.id, usuario_teste, session)
    session.refresh(registro)

    assert response == {'message': 'Registro de glosa desfeito!'}
    assert registro.sn_ativo == 'not'


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


def test_acato_aceita_campos_exclusivos_do_recurso_vazios(
    session,
    usuario_teste,
):
    payload = registro_glosa_payload(
        sn_glosado='not',
        processo_recurso=None,
        qtd_glosada=None,
        valor_glosado=None,
    )

    registro = registrar_glosa(
        RegistroGlosaCreate(**payload),
        usuario_teste,
        session,
    )

    assert registro.status_tratativa == 'acato'
    assert registro.processo_recurso is None
    assert registro.qtd_recursado is None
    assert registro.valor_recursado is None


def test_recurso_aceita_processo_vazio(session, usuario_teste):
    payload = registro_glosa_payload(
        processo_recurso=None,
    )

    registro = registrar_glosa(
        RegistroGlosaCreate(**payload),
        usuario_teste,
        session,
    )

    assert registro.processo_recurso is None
    assert registro.status_tratativa == 'recurso'


@pytest.mark.parametrize('campo', ['qtd_glosada', 'valor_glosado'])
def test_recurso_continua_exigindo_quantidade_e_valor(campo):
    payload = registro_glosa_payload(
        processo_recurso=None,
        **{campo: None},
    )

    with pytest.raises(ValueError, match='Informe quantidade e valor'):
        RegistroGlosaCreate(**payload)


def test_recurso_e_acato_coexistem_respeitando_limites_do_item(
    session,
    usuario_teste,
):
    recurso = registrar_glosa(
        RegistroGlosaCreate(
            **registro_glosa_payload(
                qtd_glosada='1',
                valor_glosado='60.00',
            )
        ),
        usuario_teste,
        session,
    )
    payload_acato = RegistroGlosaCreate(
        **registro_glosa_payload(
            sn_glosado='not',
            processo_recurso=None,
            qtd_glosada='1',
            valor_glosado='43.45',
        )
    )

    acato = editar_glosa(
        recurso.id,
        payload_acato,
        usuario_teste,
        session,
    )
    session.refresh(recurso)

    assert acato.id != recurso.id
    assert recurso.status_tratativa == 'recurso'
    assert recurso.valor_recursado == Decimal('60.00')
    assert acato.status_tratativa == 'acato'
    assert acato.valor_recursado == Decimal('43.45')

    with pytest.raises(HTTPException, match='soma das quantidades'):
        editar_glosa(
            acato.id,
            RegistroGlosaCreate(
                **registro_glosa_payload(
                    sn_glosado='not',
                    processo_recurso=None,
                    qtd_glosada='2',
                    valor_glosado='43.45',
                )
            ),
            usuario_teste,
            session,
        )

    with pytest.raises(HTTPException, match='soma dos valores'):
        editar_glosa(
            acato.id,
            RegistroGlosaCreate(
                **registro_glosa_payload(
                    sn_glosado='not',
                    processo_recurso=None,
                    qtd_glosada='1',
                    valor_glosado='43.46',
                )
            ),
            usuario_teste,
            session,
        )


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


def test_permite_registrar_recebimento_parcial(session, usuario_teste):
    registro = registrar_glosa(
        payload=RegistroGlosaCreate(
            **registro_glosa_payload(
                qtd_glosada='2',
                valor_glosado='12.31',
            )
        ),
        usuario_atual=usuario_teste,
        session=session,
    )
    registro = registrar_recebimento_glosa(
        glosa_id=registro.id,
        payload=RegistroGlosaRecebimentoUpdate(
            dt_recebimento=date(2026, 6, 20),
            valor_recebido=Decimal('5.00'),
            qtd_recebida=Decimal('1.00'),
            observacao_recebimento='Recebimento parcial',
        ),
        usuario_atual=usuario_teste,
        session=session,
    )

    assert registro.valor_recebido == Decimal('5.00')
    assert registro.qtd_recebida == Decimal('1.00')

    registro = registrar_recebimento_glosa(
        glosa_id=registro.id,
        payload=RegistroGlosaRecebimentoUpdate(
            dt_recebimento=date(2026, 6, 21),
            valor_recebido=Decimal('12.31'),
            qtd_recebida=Decimal('2.00'),
            observacao_recebimento='Recebimento integral acumulado',
        ),
        usuario_atual=usuario_teste,
        session=session,
    )

    assert registro.valor_recebido == Decimal('12.31')
    assert registro.qtd_recebida == Decimal('2.00')


def test_rejeita_reducao_do_recebimento_acumulado(session, usuario_teste):
    registro = registrar_glosa(
        payload=RegistroGlosaCreate(
            **registro_glosa_payload(
                qtd_glosada='2',
                valor_glosado='12.31',
            )
        ),
        usuario_atual=usuario_teste,
        session=session,
    )
    registrar_recebimento_glosa(
        glosa_id=registro.id,
        payload=RegistroGlosaRecebimentoUpdate(
            dt_recebimento=date(2026, 6, 20),
            valor_recebido=Decimal('5.00'),
            qtd_recebida=Decimal('1.00'),
        ),
        usuario_atual=usuario_teste,
        session=session,
    )

    with pytest.raises(HTTPException) as exc_info:
        registrar_recebimento_glosa(
            glosa_id=registro.id,
            payload=RegistroGlosaRecebimentoUpdate(
                dt_recebimento=date(2026, 6, 20),
                valor_recebido=Decimal('4.00'),
                qtd_recebida=Decimal('1.00'),
            ),
            usuario_atual=usuario_teste,
            session=session,
        )

    assert exc_info.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert 'acumulado' in exc_info.value.detail


@pytest.mark.parametrize(
    ('dt_recebimento', 'mensagem'),
    [
        ('2099-01-01', 'maior que a data atual'),
        ('2026-06-15', 'anterior ao recurso'),
    ],
)
def test_rejeita_data_invalida_no_recebimento(
    session,
    usuario_teste,
    dt_recebimento,
    mensagem,
):
    registro = registrar_glosa(
        payload=RegistroGlosaCreate(**registro_glosa_payload()),
        usuario_atual=usuario_teste,
        session=session,
    )

    with pytest.raises(HTTPException) as exc_info:
        registrar_recebimento_glosa(
            glosa_id=registro.id,
            payload=RegistroGlosaRecebimentoUpdate(
                dt_recebimento=date.fromisoformat(dt_recebimento),
                valor_recebido=Decimal('5.00'),
                qtd_recebida=Decimal('1.00'),
            ),
            usuario_atual=usuario_teste,
            session=session,
        )

    assert exc_info.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert mensagem in exc_info.value.detail


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
