import hashlib
from datetime import datetime
from decimal import Decimal
from http import HTTPStatus
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from app_prontocardio.app import app
from app_prontocardio.models import (
    EmissaoNfse,
    EmissaoNfseArquivo,
    EmpresaEmissora,
    EmpresaEmissoraEvento,
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
    EmpresaEmissoraCreate,
    EmpresaEmissoraStatusUpdate,
    EmpresaEmissoraUpdate,
    SolicitacaoNotaCreate,
    SolicitacaoNotaEmissaoFilter,
    SolicitacaoNotaEmpresaEmissoraInput,
    SolicitacaoNotaFilter,
    SolicitacaoNotaUpdate,
    SolicitacaoNotaWorkflowFilter,
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
TOTAL_SOLICITACOES_RECUSAS = 2
QUANTIDADE_LOTE = 2
EMPRESA_CNPJ = '05613278000158'
EMPRESA_RAZAO_SOCIAL = 'PRONTOCARDIO PRONTOATENDIMENTO CARDIOLOGICO LTDA'


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
        procedimentos_atendimento=[
            {
                'codigo': '40304361',
                'descricao': 'ECOCARDIOGRAMA TRANSTORÁCICO',
                'grupo': 'EXAMES CARDIOLÓGICOS',
                'quantidade': Decimal('1'),
                'valor_total': Decimal('385.50'),
                'realizado_em': datetime(2026, 7, 23, 10, 30),
                'prestador': 'DR. TESTE',
            }
        ],
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

    class ResultadoProcedimentos:
        @staticmethod
        def all():
            return [
                SimpleNamespace(
                    cd_atendimento=CODIGO_ATENDIMENTO,
                    cd_pro_fat='40304361',
                    descricao='ECOCARDIOGRAMA TRANSTORÁCICO',
                    ds_gru_fat='EXAMES CARDIOLÓGICOS',
                    qt_lancamento=Decimal('1'),
                    vl_total_conta=Decimal('385.50'),
                    dt_lancamento=datetime(2026, 7, 23, 10, 30),
                    nm_prestador='DR. TESTE',
                )
            ]

    class OracleSession:
        consultas = 0

        @classmethod
        def execute(cls, _query):
            cls.consultas += 1
            if cls.consultas == 1:
                return ResultadoAtendimento()
            return ResultadoProcedimentos()

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
        SolicitacaoNotaFilter(limit=1, offset=1),
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
    assert len(response.resumo_status) == 1
    assert response.resumo_status[0].status == 'PENDENTE_VALIDACAO'
    assert response.resumo_status[0].quantidade == TOTAL_SOLICITACOES
    assert response.resumo_status[0].valor_total == Decimal('182.25')


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
    solicitacao = requisicoes.cadastrar_solicitacao_nota(
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
    registro = session.get(SolicitacaoNota, solicitacao.id)
    registro.cnpj_emissor = EMPRESA_CNPJ
    registro.razao_social_emissor = EMPRESA_RAZAO_SOCIAL
    session.commit()
    return solicitacao


def test_empresas_emissoras_rastreiam_criacao_edicao_e_inativacao(
    session,
    usuario_teste,
):
    criada = requisicoes.cadastrar_empresa_emissora(
        EmpresaEmissoraCreate(
            cnpj=EMPRESA_CNPJ,
            razao_social=EMPRESA_RAZAO_SOCIAL,
        ),
        usuario_teste,
        session,
    )
    atualizada = requisicoes.atualizar_empresa_emissora(
        criada.id,
        EmpresaEmissoraUpdate(
            cnpj=EMPRESA_CNPJ,
            razao_social=f'{EMPRESA_RAZAO_SOCIAL} - MATRIZ',
        ),
        usuario_teste,
        session,
    )
    inativada = requisicoes.atualizar_status_empresa_emissora(
        criada.id,
        EmpresaEmissoraStatusUpdate(ativo=False),
        usuario_teste,
        session,
    )
    listagem = requisicoes.listar_empresas_emissoras(
        usuario_teste,
        session,
        incluir_inativas=True,
    )

    assert atualizada.atualizado_por == usuario_teste.nome
    assert inativada.ativo is False
    assert listagem.total == 1
    assert listagem.empresas[0].usuario_atualizacao_id == usuario_teste.id
    eventos = session.scalars(
        select(EmpresaEmissoraEvento).order_by(EmpresaEmissoraEvento.id)
    ).all()
    assert [evento.tipo_acao for evento in eventos] == [
        'CRIACAO',
        'ATUALIZACAO',
        'INATIVACAO',
    ]
    assert {evento.usuario_id for evento in eventos} == {usuario_teste.id}


def test_selecao_empresa_emissora_grava_snapshot_na_solicitacao(
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacao = _criar_solicitacao(
        session,
        usuario_teste,
        monkeypatch,
    )
    empresa = EmpresaEmissora(
        cnpj='08711085000128',
        razao_social='PRONTOCARDIO SERVICOS MEDICOS HOSPITALARES LTDA',
        usuario_criacao_id=usuario_teste.id,
        usuario_atualizacao_id=usuario_teste.id,
    )
    session.add(empresa)
    session.commit()
    session.refresh(empresa)

    response = requisicoes.selecionar_empresa_emissora_solicitacao(
        solicitacao.id,
        SolicitacaoNotaEmpresaEmissoraInput(empresa_emissora_id=empresa.id),
        usuario_teste,
        session,
    )

    assert response.empresa_emissora_id == empresa.id
    assert response.cnpj_emissor == empresa.cnpj
    assert response.razao_social_emissor == empresa.razao_social
    evento = session.scalar(
        select(SolicitacaoNotaEvento).where(
            SolicitacaoNotaEvento.tipo_acao == 'EMPRESA_EMISSORA_SELECIONADA'
        )
    )
    assert evento.usuario_id == usuario_teste.id


def test_lista_solicitacoes_aplica_filtros_e_resume_por_status(
    session,
    usuario_teste,
    monkeypatch,
):
    pendente = _criar_solicitacao(
        session,
        usuario_teste,
        monkeypatch,
        'Consulta',
    )
    validada = _criar_solicitacao(
        session,
        usuario_teste,
        monkeypatch,
        'Exame',
    )
    registro = session.get(SolicitacaoNota, validada.id)
    registro.codigo_atendimento = 987654
    registro.nm_paciente = 'JOANA CARDOSO'
    registro.convenio = 'CONVÊNIO PREMIUM'
    registro.local = 'Clinica 2'
    registro.valor_nota = Decimal('125.50')
    workflow = session.scalar(
        select(SolicitacaoNotaWorkflow).where(
            SolicitacaoNotaWorkflow.solicitacao_nota_id == validada.id
        )
    )
    workflow.status = StatusWorkflowSolicitacao.VALIDADA.value
    workflow.validacao = StatusWorkflowSolicitacao.VALIDADA.value
    session.commit()

    response = requisicoes.listar_solicitacoes_nota(
        usuario_teste,
        session,
        SolicitacaoNotaFilter(
            codigo_atendimento=987654,
            nome_paciente='joana',
            convenio='premium',
            local='Clinica 2',
            status='VALIDADA',
        ),
    )

    assert response.total == 1
    assert [item.id for item in response.solicitacoes] == [validada.id]
    assert response.solicitacoes[0].emissao_id is None
    assert response.resumo_status[0].status == 'VALIDADA'
    assert response.resumo_status[0].quantidade == 1
    assert response.resumo_status[0].valor_total == Decimal('125.50')

    por_status = requisicoes.listar_solicitacoes_nota(
        usuario_teste,
        session,
        SolicitacaoNotaFilter(status='VALIDADA'),
    )
    resumo = {
        item.status: (item.quantidade, item.valor_total)
        for item in por_status.resumo_status
    }
    assert por_status.total == 1
    assert set(resumo) == {'PENDENTE_VALIDACAO', 'VALIDADA'}
    assert resumo['PENDENTE_VALIDACAO'] == (1, Decimal('60.75'))
    assert resumo['VALIDADA'] == (1, Decimal('125.50'))
    assert pendente.id != validada.id


def test_edicao_reinicia_validacao_e_inativacao_preserva_historico(
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
        ValidacaoSolicitacaoNotaInput(
            decisao='RECUSADA',
            motivo_recusa='Valor divergente.',
        ),
        usuario_teste,
        session,
    )

    atualizada = requisicoes.atualizar_solicitacao_nota(
        solicitacao.id,
        SolicitacaoNotaUpdate(
            local='Emergencia',
            procedimento='Procedimento corrigido',
            valor_nota=Decimal('99.90'),
        ),
        usuario_teste,
        session,
    )

    assert atualizada.local == 'Emergencia'
    assert atualizada.procedimento == 'Procedimento corrigido'
    assert atualizada.valor_nota == Decimal('99.90')
    assert atualizada.status == 'PENDENTE_VALIDACAO'
    assert atualizada.validacao is None
    assert atualizada.validado_por_id is None
    evento_edicao = session.scalar(
        select(SolicitacaoNotaEvento).where(
            SolicitacaoNotaEvento.tipo_acao == 'EDICAO'
        )
    )
    assert evento_edicao.usuario_id == usuario_teste.id

    response = requisicoes.inativar_solicitacao_nota(
        solicitacao.id,
        usuario_teste,
        session,
    )

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert session.get(SolicitacaoNota, solicitacao.id).ativo is False
    evento_inativacao = session.scalar(
        select(SolicitacaoNotaEvento).where(
            SolicitacaoNotaEvento.tipo_acao == 'INATIVACAO'
        )
    )
    assert evento_inativacao.usuario_id == usuario_teste.id
    lista = requisicoes.listar_solicitacoes_nota(
        usuario_teste,
        session,
        SolicitacaoNotaFilter(),
    )
    assert lista.total == 0
    assert lista.solicitacoes == []
    assert lista.resumo_status == []


def test_fila_de_recusas_reune_recusadas_ativas_e_inativadas(
    session,
    usuario_teste,
    monkeypatch,
):
    recusada = _criar_solicitacao(
        session,
        usuario_teste,
        monkeypatch,
        'Solicitação recusada',
    )
    requisicoes.validar_solicitacao_nota(
        recusada.id,
        ValidacaoSolicitacaoNotaInput(
            decisao='RECUSADA',
            motivo_recusa='Dados divergentes.',
        ),
        usuario_teste,
        session,
    )
    inativada = _criar_solicitacao(
        session,
        usuario_teste,
        monkeypatch,
        'Solicitação inativada',
    )
    requisicoes.inativar_solicitacao_nota(
        inativada.id,
        usuario_teste,
        session,
    )

    fila = requisicoes.listar_workflow_solicitacoes_nota(
        usuario_teste,
        session,
        SolicitacaoNotaWorkflowFilter(
            status='RECUSADA',
            incluir_inativas=True,
        ),
        object(),
    )

    solicitacoes_por_id = {
        solicitacao.id: solicitacao for solicitacao in fila.solicitacoes
    }
    assert fila.total == TOTAL_SOLICITACOES_RECUSAS
    assert solicitacoes_por_id[recusada.id].ativo is True
    assert solicitacoes_por_id[recusada.id].status == 'RECUSADA'
    assert solicitacoes_por_id[inativada.id].ativo is False
    assert solicitacoes_por_id[inativada.id].status == ('PENDENTE_VALIDACAO')
    assert solicitacoes_por_id[inativada.id].inativado_por_id == (
        usuario_teste.id
    )
    assert solicitacoes_por_id[inativada.id].inativado_por == (
        usuario_teste.nome
    )
    assert solicitacoes_por_id[inativada.id].inativado_em is not None


def test_workflow_pendente_inclui_procedimentos_do_atendimento(
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacao = _criar_solicitacao(
        session,
        usuario_teste,
        monkeypatch,
    )

    class ResultadoOracle:
        @staticmethod
        def all():
            return [
                SimpleNamespace(
                    cd_atendimento=CODIGO_ATENDIMENTO,
                    cd_pro_fat='40304361',
                    descricao='ECOCARDIOGRAMA TRANSTORÁCICO',
                    ds_gru_fat='EXAMES CARDIOLÓGICOS',
                    qt_lancamento=Decimal('1'),
                    vl_total_conta=Decimal('385.50'),
                    dt_lancamento=datetime(2026, 7, 23, 10, 30),
                    nm_prestador='DR. TESTE',
                ),
                SimpleNamespace(
                    cd_atendimento=CODIGO_ATENDIMENTO,
                    cd_pro_fat='10101012',
                    descricao='CONSULTA EM CARDIOLOGIA',
                    ds_gru_fat='PROCEDIMENTOS',
                    qt_lancamento=Decimal('1'),
                    vl_total_conta=Decimal('210.00'),
                    dt_lancamento=datetime(2026, 7, 23, 9, 45),
                    nm_prestador=None,
                ),
            ]

    class OracleSession:
        @staticmethod
        def execute(_query):
            return ResultadoOracle()

    fila = requisicoes.listar_workflow_solicitacoes_nota(
        usuario_teste,
        session,
        SolicitacaoNotaWorkflowFilter(),
        OracleSession(),
    )

    assert fila.total == 1
    assert fila.solicitacoes[0].id == solicitacao.id
    assert fila.solicitacoes[0].procedimentos_atendimento_disponiveis is True
    procedimentos = fila.solicitacoes[0].procedimentos_atendimento
    assert [item.codigo for item in procedimentos] == [
        '40304361',
        '10101012',
    ]
    assert procedimentos[0].descricao == 'ECOCARDIOGRAMA TRANSTORÁCICO'
    assert procedimentos[0].grupo == 'EXAMES CARDIOLÓGICOS'
    assert procedimentos[0].quantidade == Decimal('1')
    assert procedimentos[0].valor_total == Decimal('385.50')
    assert procedimentos[0].realizado_em == datetime(
        2026,
        7,
        23,
        10,
        30,
    )
    assert procedimentos[0].prestador == 'DR. TESTE'


@pytest.mark.parametrize(
    'status',
    [
        StatusWorkflowSolicitacao.VALIDADA,
        StatusWorkflowSolicitacao.EMISSAO_SOLICITADA,
        StatusWorkflowSolicitacao.ERRO_EMISSAO,
        StatusWorkflowSolicitacao.EMITIDA,
    ],
)
def test_edicao_e_inativacao_bloqueiam_solicitacoes_ja_validadas(
    status,
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacao = _criar_solicitacao(
        session,
        usuario_teste,
        monkeypatch,
    )
    workflow = session.scalar(
        select(SolicitacaoNotaWorkflow).where(
            SolicitacaoNotaWorkflow.solicitacao_nota_id == solicitacao.id
        )
    )
    workflow.status = status.value
    workflow.validacao = StatusWorkflowSolicitacao.VALIDADA.value
    session.commit()

    with pytest.raises(HTTPException) as edicao:
        requisicoes.atualizar_solicitacao_nota(
            solicitacao.id,
            SolicitacaoNotaUpdate(
                local='Clinica 2',
                procedimento='Alteração indevida',
                valor_nota=Decimal('80.00'),
            ),
            usuario_teste,
            session,
        )
    assert edicao.value.status_code == HTTPStatus.CONFLICT

    with pytest.raises(HTTPException) as inativacao:
        requisicoes.inativar_solicitacao_nota(
            solicitacao.id,
            usuario_teste,
            session,
        )
    assert inativacao.value.status_code == HTTPStatus.CONFLICT
    assert session.get(SolicitacaoNota, solicitacao.id).ativo is True


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
        select(SolicitacaoNotaEvento).where(
            SolicitacaoNotaEvento.tipo_acao == 'VALIDADA'
        )
    )
    assert evento.usuario_id == usuario_teste.id


def test_solicitacao_validada_pode_ser_revertida_para_recusa(
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

    response = requisicoes.validar_solicitacao_nota(
        solicitacao.id,
        ValidacaoSolicitacaoNotaInput(
            decisao='RECUSADA',
            motivo_recusa='Convênio divergente.',
        ),
        usuario_teste,
        session,
    )

    assert response.status == StatusWorkflowSolicitacao.RECUSADA
    assert response.validacao == 'RECUSADA'
    assert response.motivo_recusa == 'Convênio divergente.'
    evento = session.scalar(
        select(SolicitacaoNotaEvento).where(
            SolicitacaoNotaEvento.tipo_acao == 'REVERSAO_RECUSA'
        )
    )
    assert evento is not None
    assert evento.usuario_id == usuario_teste.id
    assert evento.observacao == 'Convênio divergente.'


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
        select(SolicitacaoNotaEvento).where(
            SolicitacaoNotaEvento.tipo_acao == 'RECUSADA'
        )
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
    disparo = {}
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

    def disparar(lote_id, ids, cnpjs):
        disparo.update({
            'lote_id': lote_id,
            'ids': ids,
            'cnpjs': cnpjs,
        })
        return AirflowDagRun(
            dag_run_id=f'dag-lote-{lote_id}',
            state='queued',
        )

    monkeypatch.setattr(
        requisicoes,
        'disparar_dag_emissao_nfse',
        disparar,
    )

    response = requisicoes.solicitar_emissao_nfse(
        EmissaoNfseCreate(
            solicitacao_ids=[solicitacao.id for solicitacao in solicitacoes]
        ),
        usuario_teste,
        session,
    )

    assert response.tipo == 'LOTE'
    assert response.quantidade == QUANTIDADE_LOTE
    assert response.dag_run_id == f'dag-lote-{response.lote_id}'
    assert disparo == {
        'lote_id': response.lote_id,
        'ids': [solicitacao.id for solicitacao in solicitacoes],
        'cnpjs': {
            solicitacao.id: EMPRESA_CNPJ for solicitacao in solicitacoes
        },
    }
    assert (
        session.scalar(select(func.count()).select_from(EmissaoNfse))
        == QUANTIDADE_LOTE
    )
    assert session.get(LoteEmissaoNfse, response.lote_id).status == (
        'PROCESSANDO'
    )
    statuses = session.scalars(select(SolicitacaoNotaWorkflow.status)).all()
    assert set(statuses) == {'EMISSAO_SOLICITADA'}
    eventos_emissao = session.scalars(
        select(SolicitacaoNotaEvento).where(
            SolicitacaoNotaEvento.tipo_acao == 'EMISSAO_SOLICITADA'
        )
    ).all()
    assert len(eventos_emissao) == QUANTIDADE_LOTE
    assert {evento.usuario_id for evento in eventos_emissao} == {
        usuario_teste.id
    }


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

    def falhar_disparo(_lote_id, _ids, _cnpjs):
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
        lambda lote_id, _ids, _cnpjs: AirflowDagRun(
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
    assert session.scalar(select(func.count()).select_from(EmissaoNfse)) == 0


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

    def disparar(lote_id, _ids, _cnpjs):
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
    assert (
        session.scalar(select(func.count()).select_from(LoteEmissaoNfse)) == 1
    )
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

    def timeout(_lote_id, _ids, _cnpjs):
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
        lambda lote_id, _ids, _cnpjs: AirflowDagRun(
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


def _solicitar_emissao_teste(
    session,
    usuario_teste,
    monkeypatch,
    solicitacao,
):
    monkeypatch.setattr(
        requisicoes,
        'disparar_dag_emissao_nfse',
        lambda lote_id, _ids, _cnpjs: AirflowDagRun(
            dag_run_id=f'run-{lote_id}'
        ),
    )
    return requisicoes.solicitar_emissao_nfse(
        EmissaoNfseCreate(solicitacao_ids=[solicitacao.id]),
        usuario_teste,
        session,
    )


def _criar_emissao_emitida(
    session,
    usuario_teste,
    monkeypatch,
    *,
    com_arquivo=True,
):
    solicitacao = _preparar_emissao(
        session,
        usuario_teste,
        monkeypatch,
    )
    lote_public = _solicitar_emissao_teste(
        session,
        usuario_teste,
        monkeypatch,
        solicitacao,
    )
    emissao = session.scalar(
        select(EmissaoNfse).where(EmissaoNfse.lote_id == lote_public.lote_id)
    )
    workflow = session.scalar(
        select(SolicitacaoNotaWorkflow).where(
            SolicitacaoNotaWorkflow.solicitacao_nota_id == solicitacao.id
        )
    )
    lote = session.get(LoteEmissaoNfse, lote_public.lote_id)
    emissao.status = 'EMITIDA'
    emissao.numero_nfse = '98765'
    emissao.protocolo = 'PROTOCOLO-98765'
    workflow.status = 'EMITIDA'
    lote.status = 'EMITIDA'
    arquivo = None
    if com_arquivo:
        conteudo = b'%PDF-1.7\nconteudo de teste'
        arquivo = EmissaoNfseArquivo(
            emissao_nfse_id=emissao.id,
            nome_arquivo='98765 - MARIA DA SILVA.pdf',
            tipo_mime='application/pdf',
            conteudo=conteudo,
            tamanho_bytes=len(conteudo),
            sha256=hashlib.sha256(conteudo).hexdigest(),
        )
        session.add(arquivo)
    session.commit()
    session.refresh(emissao)
    if arquivo:
        session.refresh(arquivo)
    return solicitacao, emissao, arquivo


def test_fila_emissao_mantem_item_solicitado_e_usa_tentativa_mais_recente(
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacao = _preparar_emissao(
        session,
        usuario_teste,
        monkeypatch,
    )
    lote_anterior = LoteEmissaoNfse(
        tipo='INDIVIDUAL',
        usuario_id=usuario_teste.id,
        status='ERRO',
    )
    session.add(lote_anterior)
    session.flush()
    tentativa_anterior = EmissaoNfse(
        solicitacao_nota_id=solicitacao.id,
        lote_id=lote_anterior.id,
        usuario_id=usuario_teste.id,
        status='ERRO',
        erro='Falha anterior.',
    )
    session.add(tentativa_anterior)
    session.commit()

    lote_atual = _solicitar_emissao_teste(
        session,
        usuario_teste,
        monkeypatch,
        solicitacao,
    )
    emissao_atual = session.scalar(
        select(EmissaoNfse).where(EmissaoNfse.lote_id == lote_atual.lote_id)
    )

    response = requisicoes.listar_emissoes_nfse(
        usuario_teste,
        session,
        SolicitacaoNotaEmissaoFilter(),
    )

    assert response.total == 1
    assert len(response.solicitacoes) == 1
    item = response.solicitacoes[0]
    assert item.id == solicitacao.id
    assert item.status == 'EMISSAO_SOLICITADA'
    assert item.emissao_id == emissao_atual.id
    assert item.status_emissao == 'PENDENTE'
    assert item.erro_emissao is None
    assert item.arquivo_disponivel is False


def test_fila_emissao_exibe_numero_e_pdf_da_nfse_emitida(
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacao, emissao, _arquivo = _criar_emissao_emitida(
        session,
        usuario_teste,
        monkeypatch,
    )

    response = requisicoes.listar_emissoes_nfse(
        usuario_teste,
        session,
        SolicitacaoNotaEmissaoFilter(
            nome_paciente='maria',
            cpf='456789',
            tipo_atendimento='Ambulatório',
            local='Clinica 1',
        ),
    )

    assert response.total == 1
    item = response.solicitacoes[0]
    assert item.id == solicitacao.id
    assert item.status == 'EMITIDA'
    assert item.emissao_id == emissao.id
    assert item.numero_nfse == '98765'
    assert item.protocolo == 'PROTOCOLO-98765'
    assert item.arquivo_disponivel is True

    cadastradas = requisicoes.listar_solicitacoes_nota(
        usuario_teste,
        session,
        SolicitacaoNotaFilter(status='EMITIDA'),
    )
    assert cadastradas.total == 1
    cadastro = cadastradas.solicitacoes[0]
    assert cadastro.id == solicitacao.id
    assert cadastro.emissao_id == emissao.id
    assert cadastro.numero_nfse == '98765'
    assert cadastro.protocolo == 'PROTOCOLO-98765'
    assert cadastro.arquivo_disponivel is True

    sem_resultado = requisicoes.listar_emissoes_nfse(
        usuario_teste,
        session,
        SolicitacaoNotaEmissaoFilter(nome_paciente='OUTRA PESSOA'),
    )
    assert sem_resultado.total == 0
    assert sem_resultado.solicitacoes == []


def test_fila_emissao_pagina_solicitacoes_validadas_sem_emissao(
    session,
    usuario_teste,
    monkeypatch,
):
    for procedimento in ('Consulta', 'Exame'):
        _preparar_emissao(
            session,
            usuario_teste,
            monkeypatch,
            procedimento,
        )

    response = requisicoes.listar_emissoes_nfse(
        usuario_teste,
        session,
        SolicitacaoNotaEmissaoFilter(limit=1, offset=1),
    )

    assert response.total == QUANTIDADE_LOTE
    assert response.limit == 1
    assert response.offset == 1
    assert len(response.solicitacoes) == 1
    assert response.solicitacoes[0].status == 'VALIDADA'
    assert response.solicitacoes[0].emissao_id is None


@pytest.mark.parametrize(
    'cenario',
    [
        (False, 'inline', 'VISUALIZACAO_NFSE'),
        (True, 'attachment', 'DOWNLOAD_NFSE'),
    ],
)
def test_pdf_emitido_pode_ser_visualizado_ou_baixado_com_auditoria(
    cenario,
    session,
    usuario_teste,
    monkeypatch,
):
    download, disposicao, tipo_acao = cenario
    solicitacao, emissao, arquivo = _criar_emissao_emitida(
        session,
        usuario_teste,
        monkeypatch,
    )

    response = requisicoes.consultar_pdf_emissao_nfse(
        emissao.id,
        usuario_teste,
        session,
        download=download,
    )

    assert response.status_code == HTTPStatus.OK
    assert response.media_type == 'application/pdf'
    assert response.body == arquivo.conteudo
    assert response.headers['content-disposition'].startswith(disposicao)
    assert (
        "filename*=UTF-8''98765%20-%20MARIA%20DA%20SILVA.pdf"
        in (response.headers['content-disposition'])
    )
    evento = session.scalar(
        select(SolicitacaoNotaEvento).where(
            SolicitacaoNotaEvento.solicitacao_nota_id == solicitacao.id,
            SolicitacaoNotaEvento.tipo_acao == tipo_acao,
        )
    )
    assert evento is not None
    assert evento.usuario_id == usuario_teste.id
    assert 'NFS-e 98765' in evento.observacao


def test_pdf_exige_emissao_concluida(
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacao = _preparar_emissao(
        session,
        usuario_teste,
        monkeypatch,
    )
    lote = _solicitar_emissao_teste(
        session,
        usuario_teste,
        monkeypatch,
        solicitacao,
    )
    emissao = session.scalar(
        select(EmissaoNfse).where(EmissaoNfse.lote_id == lote.lote_id)
    )

    with pytest.raises(HTTPException) as exc_info:
        requisicoes.consultar_pdf_emissao_nfse(
            emissao.id,
            usuario_teste,
            session,
        )

    assert exc_info.value.status_code == HTTPStatus.CONFLICT


def test_pdf_emitido_sem_arquivo_retorna_404(
    session,
    usuario_teste,
    monkeypatch,
):
    _solicitacao, emissao, _arquivo = _criar_emissao_emitida(
        session,
        usuario_teste,
        monkeypatch,
        com_arquivo=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        requisicoes.consultar_pdf_emissao_nfse(
            emissao.id,
            usuario_teste,
            session,
        )

    assert exc_info.value.status_code == HTTPStatus.NOT_FOUND


def test_pdf_corrompido_nao_e_entregue_nem_auditado(
    session,
    usuario_teste,
    monkeypatch,
):
    solicitacao, emissao, arquivo = _criar_emissao_emitida(
        session,
        usuario_teste,
        monkeypatch,
    )
    arquivo.conteudo = b'<html>arquivo invalido</html>'
    arquivo.tamanho_bytes = len(arquivo.conteudo)
    arquivo.sha256 = hashlib.sha256(arquivo.conteudo).hexdigest()
    session.commit()

    with pytest.raises(HTTPException) as exc_info:
        requisicoes.consultar_pdf_emissao_nfse(
            emissao.id,
            usuario_teste,
            session,
        )

    assert exc_info.value.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    evento = session.scalar(
        select(SolicitacaoNotaEvento).where(
            SolicitacaoNotaEvento.solicitacao_nota_id == solicitacao.id,
            SolicitacaoNotaEvento.tipo_acao == 'VISUALIZACAO_NFSE',
        )
    )
    assert evento is None
    assert (
        exc_info.value.detail
        == 'O arquivo PDF da NFS-e está inválido ou corrompido.'
    )


def test_endpoint_pdf_exige_autenticacao():
    rota = next(
        rota
        for rota in app.routes
        if rota.path
        == '/app_glosas/requisicoes/emissoes-nfse/itens/{emissao_id}/pdf'
    )

    dependencias = {
        dependencia.call for dependencia in rota.dependant.dependencies
    }
    assert requisicoes.valida_token_usuario_atual in dependencias
