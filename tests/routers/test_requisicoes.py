from http import HTTPStatus
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app_prontocardio.models import SolicitacaoNota
from app_prontocardio.routers import requisicoes
from app_prontocardio.schema import (
    AtendimentoSolicitacaoNotaPublic,
    SolicitacaoNotaCreate,
)

CODIGO_ATENDIMENTO = 123456
CODIGO_CONVENIO = 20
TOTAL_SOLICITACOES = 3


def dados_atendimento():
    return AtendimentoSolicitacaoNotaPublic(
        codigo_atendimento=CODIGO_ATENDIMENTO,
        codigo_paciente=789,
        codigo_convenio=CODIGO_CONVENIO,
        nm_paciente='MARIA DA SILVA',
        convenio='CONVÊNIO TESTE',
        nr_cpf='12345678901',
        nr_cep='60000000',
        ds_endereco='RUA TESTE',
        nr_endereco='100',
        nm_bairro='CENTRO',
        ds_complemento='APTO 10',
        email='maria@example.com',
        nr_fone='85999999999',
        tipo_atendimento='Ambulatório',
    )


def test_consulta_atendimento_combina_conta_e_paciente():
    class ResultadoAtendimento:
        @staticmethod
        def first():
            return SimpleNamespace(
                cd_paciente=789,
                nm_paciente='MARIA DA SILVA',
                cd_convenio=CODIGO_CONVENIO,
                nm_convenio='CONVÊNIO TESTE',
                tp_atendimento='Ambulatório',
            )

    class OracleSession:
        @staticmethod
        def execute(_query):
            return ResultadoAtendimento()

        @staticmethod
        def scalar(_query):
            return SimpleNamespace(
                paciente='MARIA DA SILVA',
                cpf='12345678901',
                cep='60000000',
                rua='RUA TESTE',
                numero_casa=100,
                bairro='CENTRO',
                complemento='APTO 10',
                email='maria@example.com',
                contato='85999999999',
            )

    response = requisicoes._consultar_atendimento(
        CODIGO_ATENDIMENTO,
        OracleSession(),
    )

    assert response == dados_atendimento()


def test_consulta_atendimento_inexistente_retorna_404():
    class ResultadoAtendimento:
        @staticmethod
        def first():
            return None

    class OracleSession:
        @staticmethod
        def execute(_query):
            return ResultadoAtendimento()

    with pytest.raises(HTTPException) as exc_info:
        requisicoes._consultar_atendimento(999999, OracleSession())

    assert exc_info.value.status_code == HTTPStatus.NOT_FOUND
    assert exc_info.value.detail == 'Código de atendimento não encontrado.'


def test_local_da_solicitacao_deve_ser_opcao_permitida():
    with pytest.raises(ValidationError):
        SolicitacaoNotaCreate(
            codigo_atendimento=CODIGO_ATENDIMENTO,
            local='Outro local',
            procedimento='Consulta',
        )


def test_cadastro_busca_novamente_oracle_e_grava_snapshot(
    session,
    usuario_teste,
    monkeypatch,
):
    monkeypatch.setattr(
        requisicoes,
        '_consultar_atendimento',
        lambda _codigo, _session: dados_atendimento(),
    )

    response = requisicoes.cadastrar_solicitacao_nota(
        SolicitacaoNotaCreate(
            codigo_atendimento=123456,
            local='Clinica 2',
            procedimento='  Exame cardiológico  ',
        ),
        usuario_teste,
        session,
        object(),
    )

    registro = session.get(SolicitacaoNota, response.id)
    assert registro is not None
    assert registro.codigo_atendimento == CODIGO_ATENDIMENTO
    assert registro.nm_paciente == 'MARIA DA SILVA'
    assert registro.codigo_convenio == CODIGO_CONVENIO
    assert registro.convenio == 'CONVÊNIO TESTE'
    assert registro.nr_fone == '85999999999'
    assert registro.local == 'Clinica 2'
    assert registro.procedimento == 'Exame cardiológico'
    assert registro.tipo_atendimento == 'Ambulatório'
    assert registro.usuario_id == usuario_teste.id


def test_lista_solicitacoes_com_paginacao(
    session,
    usuario_teste,
    monkeypatch,
):
    monkeypatch.setattr(
        requisicoes,
        '_consultar_atendimento',
        lambda _codigo, _session: dados_atendimento(),
    )
    ids = []
    for procedimento in ('Consulta', 'Exame', 'Retorno'):
        registro = requisicoes.cadastrar_solicitacao_nota(
            SolicitacaoNotaCreate(
                codigo_atendimento=CODIGO_ATENDIMENTO,
                local='Clinica 1',
                procedimento=procedimento,
            ),
            usuario_teste,
            session,
            object(),
        )
        ids.append(registro.id)

    response = requisicoes.listar_solicitacoes_nota(
        usuario_teste,
        session,
        limit=1,
        offset=1,
    )

    assert response.total == TOTAL_SOLICITACOES
    assert response.limit == 1
    assert response.offset == 1
    assert len(response.solicitacoes) == 1
    assert response.solicitacoes[0].id == ids[-2]
