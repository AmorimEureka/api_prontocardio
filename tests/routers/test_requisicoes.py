from decimal import Decimal
from http import HTTPStatus
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from app_prontocardio.models import (
    EmissaoNfse,
    LoteEmissaoNfse,
    SolicitacaoNota,
    SolicitacaoNotaEvento,
    SolicitacaoNotaWorkflow,
    StatusWorkflowSolicitacao,
)
from app_prontocardio.routers import requisicoes
from app_prontocardio.schema import (
    AtendimentoSolicitacaoNotaPublic,
    EmissaoNfseCreate,
    SolicitacaoNotaCreate,
    ValidacaoSolicitacaoNotaInput,
)
from app_prontocardio.services.airflow_nfse import (
    AirflowDagRun,
    AirflowNfseIndisponivelError,
    AirflowNfseTriggerError,
)

CODIGO_ATENDIMENTO = 123456
CODIGO_CONVENIO = 20
TOTAL_SOLICITACOES = 3
QUANTIDADE_LOTE = 2


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
            valor_nota=Decimal('60.75'),
        )


def test_valor_da_nota_deve_ser_informado_no_cadastro():
    with pytest.raises(ValidationError):
        SolicitacaoNotaCreate(
            codigo_atendimento=CODIGO_ATENDIMENTO,
            local='Clinica 1',
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
            valor_nota=Decimal('60.75'),
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
    assert registro.valor_nota == Decimal('60.75')
    assert registro.local == 'Clinica 2'
    assert registro.procedimento == 'Exame cardiológico'
    assert registro.tipo_atendimento == 'Ambulatório'
    assert registro.usuario_id == usuario_teste.id
    assert response.cadastrado_por == usuario_teste.nome
    workflow = session.scalar(
        select(SolicitacaoNotaWorkflow).where(
            SolicitacaoNotaWorkflow.solicitacao_nota_id == registro.id
        )
    )
    assert workflow.status == StatusWorkflowSolicitacao.PENDENTE_VALIDACAO
    evento = session.scalar(select(SolicitacaoNotaEvento))
    assert evento.usuario_id == usuario_teste.id
    assert evento.tipo_acao == 'CRIACAO'
    assert '60.75' in evento.observacao


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
                valor_nota=Decimal('60.75'),
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
    assert response.solicitacoes[0].valor_nota == Decimal('60.75')
    assert response.solicitacoes[0].cadastrado_por == usuario_teste.nome
    assert response.solicitacoes[0].status == (
        StatusWorkflowSolicitacao.PENDENTE_VALIDACAO
    )


def _criar_solicitacao(
    session,
    usuario_teste,
    monkeypatch,
    procedimento='Consulta',
):
    monkeypatch.setattr(
        requisicoes,
        '_consultar_atendimento',
        lambda _codigo, _session: dados_atendimento(),
    )
    return requisicoes.cadastrar_solicitacao_nota(
        SolicitacaoNotaCreate(
            codigo_atendimento=CODIGO_ATENDIMENTO,
            local='Clinica 1',
            procedimento=procedimento,
            valor_nota=Decimal('60.75'),
        ),
        usuario_teste,
        session,
        object(),
    )


def test_validacao_move_solicitacao_para_fila_de_emissao(
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacao = _criar_solicitacao(
        session,
        usuario_teste,
        monkeypatch,
    )

    response = requisicoes.validar_solicitacao_nota(
        solicitacao.id,
        ValidacaoSolicitacaoNotaInput(decisao='VALIDADA'),
        usuario_teste,
        session,
    )

    assert response.status == StatusWorkflowSolicitacao.VALIDADA
    assert response.cadastrado_por == usuario_teste.nome
    assert response.validado_por_id == usuario_teste.id
    assert response.validado_por == usuario_teste.nome
    evento = session.scalar(
        select(SolicitacaoNotaEvento)
        .where(SolicitacaoNotaEvento.tipo_acao == 'VALIDADA')
    )
    assert evento.usuario_id == usuario_teste.id


def test_recusa_exige_motivo_e_fica_na_fila_de_recusadas(
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacao = _criar_solicitacao(
        session,
        usuario_teste,
        monkeypatch,
    )

    response = requisicoes.validar_solicitacao_nota(
        solicitacao.id,
        ValidacaoSolicitacaoNotaInput(
            decisao='RECUSADA',
            motivo_recusa='CPF divergente.',
        ),
        usuario_teste,
        session,
    )

    assert response.status == StatusWorkflowSolicitacao.RECUSADA
    assert response.motivo_recusa == 'CPF divergente.'
    evento = session.scalar(
        select(SolicitacaoNotaEvento)
        .where(SolicitacaoNotaEvento.tipo_acao == 'RECUSADA')
    )
    assert evento.usuario_id == usuario_teste.id


def test_validacao_bloqueia_registro_legado_sem_valor(
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacao = _criar_solicitacao(
        session,
        usuario_teste,
        monkeypatch,
    )
    registro = session.get(SolicitacaoNota, solicitacao.id)
    registro.valor_nota = None
    session.commit()

    with pytest.raises(HTTPException) as exc_info:
        requisicoes.validar_solicitacao_nota(
            solicitacao.id,
            ValidacaoSolicitacaoNotaInput(decisao='VALIDADA'),
            usuario_teste,
            session,
        )

    assert exc_info.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert exc_info.value.detail == (
        'Informe o valor da nota antes de validar a solicitação.'
    )


def test_emissao_em_lote_dispara_airflow_e_marca_itens(
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacoes = [
        _criar_solicitacao(
            session,
            usuario_teste,
            monkeypatch,
            procedimento,
        )
        for procedimento in ('Consulta', 'Exame')
    ]
    for solicitacao in solicitacoes:
        requisicoes.validar_solicitacao_nota(
            solicitacao.id,
            ValidacaoSolicitacaoNotaInput(decisao='VALIDADA'),
            usuario_teste,
            session,
        )
    monkeypatch.setattr(
        requisicoes,
        'airflow_nfse_configurado',
        lambda: True,
    )
    monkeypatch.setattr(
        requisicoes,
        'disparar_dag_emissao_nfse',
        lambda lote_id, _ids: AirflowDagRun(
            dag_run_id=f'dag-lote-{lote_id}',
            state='queued',
        ),
    )

    response = requisicoes.solicitar_emissao_nfse(
        EmissaoNfseCreate(
            solicitacao_ids=[
                solicitacao.id for solicitacao in solicitacoes
            ]
        ),
        usuario_teste,
        session,
    )

    assert response.tipo == 'LOTE'
    assert response.quantidade == QUANTIDADE_LOTE
    assert response.dag_run_id == f'dag-lote-{response.lote_id}'
    assert session.scalar(
        select(func.count()).select_from(EmissaoNfse)
    ) == QUANTIDADE_LOTE
    assert session.get(LoteEmissaoNfse, response.lote_id).status == (
        'PROCESSANDO'
    )
    statuses = session.scalars(
        select(SolicitacaoNotaWorkflow.status)
    ).all()
    assert set(statuses) == {'EMISSAO_SOLICITADA'}
    eventos_emissao = session.scalars(
        select(SolicitacaoNotaEvento).where(
            SolicitacaoNotaEvento.tipo_acao == 'EMISSAO_SOLICITADA'
        )
    ).all()
    assert len(eventos_emissao) == QUANTIDADE_LOTE
    assert {
        evento.usuario_id for evento in eventos_emissao
    } == {usuario_teste.id}


def test_falha_no_airflow_devolve_itens_para_emissao(
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacao = _criar_solicitacao(
        session,
        usuario_teste,
        monkeypatch,
    )
    requisicoes.validar_solicitacao_nota(
        solicitacao.id,
        ValidacaoSolicitacaoNotaInput(decisao='VALIDADA'),
        usuario_teste,
        session,
    )
    monkeypatch.setattr(
        requisicoes,
        'airflow_nfse_configurado',
        lambda: True,
    )

    def falhar_disparo(_lote_id, _ids):
        raise AirflowNfseTriggerError('Airflow indisponível.')

    monkeypatch.setattr(
        requisicoes,
        'disparar_dag_emissao_nfse',
        falhar_disparo,
    )

    with pytest.raises(HTTPException) as exc_info:
        requisicoes.solicitar_emissao_nfse(
            EmissaoNfseCreate(solicitacao_ids=[solicitacao.id]),
            usuario_teste,
            session,
        )

    assert exc_info.value.status_code == HTTPStatus.BAD_GATEWAY
    workflow = session.scalar(select(SolicitacaoNotaWorkflow))
    assert workflow.status == StatusWorkflowSolicitacao.VALIDADA
    lote = session.scalar(select(LoteEmissaoNfse))
    emissao = session.scalar(select(EmissaoNfse))
    assert lote.status == 'ERRO'
    assert emissao.status == 'ERRO'
    evento = session.scalar(
        select(SolicitacaoNotaEvento).where(
            SolicitacaoNotaEvento.tipo_acao == 'ERRO_DISPARO_EMISSAO'
        )
    )
    assert evento.usuario_id == usuario_teste.id


def _preparar_emissao(
    session,
    usuario_teste,
    monkeypatch,
    procedimento='Consulta',
):
    solicitacao = _criar_solicitacao(
        session,
        usuario_teste,
        monkeypatch,
        procedimento,
    )
    requisicoes.validar_solicitacao_nota(
        solicitacao.id,
        ValidacaoSolicitacaoNotaInput(decisao='VALIDADA'),
        usuario_teste,
        session,
    )
    monkeypatch.setattr(
        requisicoes,
        'airflow_nfse_configurado',
        lambda: True,
    )
    return solicitacao


def test_emissao_individual_persiste_dados_do_disparo(
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacao = _preparar_emissao(
        session,
        usuario_teste,
        monkeypatch,
    )
    monkeypatch.setattr(
        requisicoes,
        'disparar_dag_emissao_nfse',
        lambda lote_id, _ids: AirflowDagRun(
            dag_run_id=f'run-{lote_id}',
            state='queued',
        ),
    )

    response = requisicoes.solicitar_emissao_nfse(
        EmissaoNfseCreate(solicitacao_ids=[solicitacao.id]),
        usuario_teste,
        session,
    )

    assert response.tipo == 'INDIVIDUAL'
    assert response.status == 'PROCESSANDO'
    assert response.airflow_disparado_em is not None
    assert response.dag_run_id == f'run-{response.lote_id}'
    assert response.emissoes[0].status == 'PENDENTE'
    assert response.emissoes[0].usuario_id == usuario_teste.id
    evento = session.scalar(
        select(SolicitacaoNotaEvento).where(
            SolicitacaoNotaEvento.tipo_acao == 'AIRFLOW_DISPARADO'
        )
    )
    assert response.dag_run_id in evento.observacao


@pytest.mark.parametrize(
    'campo',
    [
        'nr_cpf',
        'nm_paciente',
        'procedimento',
        'tipo_atendimento',
    ],
)
def test_emissao_exige_dados_fiscais_obrigatorios(
    campo,
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacao = _preparar_emissao(
        session,
        usuario_teste,
        monkeypatch,
    )
    setattr(session.get(SolicitacaoNota, solicitacao.id), campo, ' ')
    session.commit()

    with pytest.raises(HTTPException) as exc_info:
        requisicoes.solicitar_emissao_nfse(
            EmissaoNfseCreate(solicitacao_ids=[solicitacao.id]),
            usuario_teste,
            session,
        )

    assert exc_info.value.status_code == HTTPStatus.CONFLICT
    assert session.scalar(
        select(func.count()).select_from(EmissaoNfse)
    ) == 0


def test_emissao_exige_validacao_e_status_validado(
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacao = _preparar_emissao(
        session,
        usuario_teste,
        monkeypatch,
    )
    workflow = session.scalar(select(SolicitacaoNotaWorkflow))
    workflow.validacao = None
    session.commit()

    with pytest.raises(HTTPException) as exc_info:
        requisicoes.solicitar_emissao_nfse(
            EmissaoNfseCreate(solicitacao_ids=[solicitacao.id]),
            usuario_teste,
            session,
        )

    assert exc_info.value.status_code == HTTPStatus.CONFLICT


def test_emissao_exige_valor_positivo(
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacao = _preparar_emissao(
        session,
        usuario_teste,
        monkeypatch,
    )
    session.get(SolicitacaoNota, solicitacao.id).valor_nota = None
    session.commit()

    with pytest.raises(HTTPException) as exc_info:
        requisicoes.solicitar_emissao_nfse(
            EmissaoNfseCreate(solicitacao_ids=[solicitacao.id]),
            usuario_teste,
            session,
        )

    assert exc_info.value.status_code == HTTPStatus.CONFLICT


def test_emissao_ativa_nao_cria_outro_lote(
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacao = _preparar_emissao(
        session,
        usuario_teste,
        monkeypatch,
    )
    disparos = 0

    def disparar(lote_id, _ids):
        nonlocal disparos
        disparos += 1
        return AirflowDagRun(dag_run_id=f'run-{lote_id}')

    monkeypatch.setattr(
        requisicoes,
        'disparar_dag_emissao_nfse',
        disparar,
    )
    requisicoes.solicitar_emissao_nfse(
        EmissaoNfseCreate(solicitacao_ids=[solicitacao.id]),
        usuario_teste,
        session,
    )
    workflow = session.scalar(select(SolicitacaoNotaWorkflow))
    workflow.status = StatusWorkflowSolicitacao.VALIDADA.value
    session.commit()

    with pytest.raises(HTTPException) as exc_info:
        requisicoes.solicitar_emissao_nfse(
            EmissaoNfseCreate(solicitacao_ids=[solicitacao.id]),
            usuario_teste,
            session,
        )

    assert exc_info.value.status_code == HTTPStatus.CONFLICT
    assert session.scalar(
        select(func.count()).select_from(LoteEmissaoNfse)
    ) == 1
    assert disparos == 1


def test_timeout_airflow_retorna_503_e_restaura_workflow(
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacao = _preparar_emissao(
        session,
        usuario_teste,
        monkeypatch,
    )

    def timeout(_lote_id, _ids):
        raise AirflowNfseIndisponivelError('Tempo limite excedido.')

    monkeypatch.setattr(
        requisicoes,
        'disparar_dag_emissao_nfse',
        timeout,
    )

    with pytest.raises(HTTPException) as exc_info:
        requisicoes.solicitar_emissao_nfse(
            EmissaoNfseCreate(solicitacao_ids=[solicitacao.id]),
            usuario_teste,
            session,
        )

    assert exc_info.value.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    workflow = session.scalar(select(SolicitacaoNotaWorkflow))
    assert workflow.status == StatusWorkflowSolicitacao.VALIDADA


def test_consulta_andamento_do_lote(
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacao = _preparar_emissao(
        session,
        usuario_teste,
        monkeypatch,
    )
    monkeypatch.setattr(
        requisicoes,
        'disparar_dag_emissao_nfse',
        lambda lote_id, _ids: AirflowDagRun(
            dag_run_id=f'run-{lote_id}'
        ),
    )
    criado = requisicoes.solicitar_emissao_nfse(
        EmissaoNfseCreate(solicitacao_ids=[solicitacao.id]),
        usuario_teste,
        session,
    )
    emissao = session.scalar(select(EmissaoNfse))
    emissao.status = 'EMITIDA'
    emissao.numero_nfse = '1234'
    emissao.protocolo = 'PROTOCOLO-1'
    session.commit()

    response = requisicoes.consultar_emissao_nfse(
        criado.lote_id,
        usuario_teste,
        session,
    )

    assert response.lote_id == criado.lote_id
    assert response.dag_run_id == f'run-{criado.lote_id}'
    assert response.quantidade == 1
    assert response.emissoes[0].numero_nfse == '1234'
    assert response.emissoes[0].protocolo == 'PROTOCOLO-1'
    assert response.emissoes[0].data_atualizacao is not None


def test_consulta_lote_inexistente_retorna_404(
    session,
    usuario_teste,
):
    with pytest.raises(HTTPException) as exc_info:
        requisicoes.consultar_emissao_nfse(
            999,
            usuario_teste,
            session,
        )

    assert exc_info.value.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize(
    'solicitacao_ids',
    [
        [],
        [1, 1],
        [0],
    ],
)
def test_payload_emissao_rejeita_lista_invalida(solicitacao_ids):
    with pytest.raises(ValidationError):
        EmissaoNfseCreate(solicitacao_ids=solicitacao_ids)
