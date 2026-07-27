import base64
import hashlib
import hmac
import logging
import os
import time
from types import SimpleNamespace
from datetime import date, datetime, timedelta
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app_prontocardio.agendamento_schema import (
    AgendamentoCancelado,
    AgendamentoConfirmado,
    AgendamentoReagendado,
    AgendamentosPaciente,
    AtualizacaoPacienteInput,
    CadastroPacienteInput,
    CancelarAgendamentoInput,
    ConfirmarAgendamentoInput,
    ConveniosPaciente,
    HorariosDisponiveis,
    HistoricoPaciente,
    ItensAgendamentoEncontrados,
    LinhasCuidadoPaciente,
    OrientacaoExame,
    PacienteAtualizado,
    PacienteCadastrado,
    PacientesEncontrados,
    PlanosDisponiveis,
    PrestadoresAgendamento,
    TiposMarcacaoConsulta,
    PreValidacaoAgendamento,
    PreValidacaoAgendamentoInput,
    ReagendarAgendamentoInput,
)
from app_prontocardio.database import get_session_oracle, get_session_postgres, postgres_engine
from app_prontocardio.models import AuditoriaAgendamento, Usuario
from app_prontocardio.security import valida_token_usuario_atual
from app_prontocardio.whatsapp_service import enviar_template_whatsapp

router = APIRouter(prefix='/agendamentos', tags=['agendamentos'])
logger = logging.getLogger(__name__)


def _observacao_agendamento(texto: str | None) -> str | None:
    if not texto:
        return None
    normalizada = ' '.join(texto.strip().split())
    return normalizada[:600] if normalizada else None


def _anexa_observacao(texto_atual: str | None, observacao: str) -> str:
    atual = (texto_atual or '').strip()
    if atual and observacao not in atual:
        return f'{atual} | {observacao}'[:600]
    return (atual or observacao)[:600]


def _nome_unidade_exibicao(nome: str | None) -> str:
    unidade = (nome or '').strip()
    unidade_normalizada = unidade.upper()
    if unidade_normalizada == 'CLINICA PRONTOCARDIO SAUDE':
        return 'CLINICA DIAGNOSTICA 01'
    if unidade_normalizada == 'UNIDADE DIAGNOSTICO 2':
        return 'CLINICA DIAGNOSTICA 02'
    return unidade or 'ProntoCardio'


def _telefone_paciente_whatsapp(
    ddd_payload: str | None,
    celular_payload: str | None,
    ddd_banco: str | None,
    celular_banco: str | None,
) -> str | None:
    ddd = ''.join(ch for ch in str(ddd_payload or ddd_banco or '') if ch.isdigit())
    celular = ''.join(
        ch for ch in str(celular_payload or celular_banco or '') if ch.isdigit()
    )
    if not ddd or not celular:
        return None
    return f'55{ddd}{celular}'

_oauth2_optional = OAuth2PasswordBearer(
    tokenUrl='/autenticacao/token', auto_error=False
)
_AGENDAMENTO_COOKIE = 'agendamento_sessao'
_AGENDAMENTO_TTL = 8 * 60 * 60


def _sessao_assinada(expira: int) -> str:
    corpo = str(expira)
    assinatura = hmac.new(
        os.getenv('SECRET_KEY', '').encode(), corpo.encode(), hashlib.sha256
    ).hexdigest()
    return base64.urlsafe_b64encode(f'{corpo}.{assinatura}'.encode()).decode()


def _sessao_valida(valor: str | None) -> bool:
    if not valor:
        return False
    try:
        corpo, assinatura = base64.urlsafe_b64decode(valor.encode()).decode().split('.', 1)
        esperado = hmac.new(
            os.getenv('SECRET_KEY', '').encode(), corpo.encode(), hashlib.sha256
        ).hexdigest()
        return int(corpo) >= int(time.time()) and hmac.compare_digest(assinatura, esperado)
    except (ValueError, TypeError, UnicodeDecodeError):
        return False


def valida_acesso_agendamento(
    request: Request,
    session=Depends(get_session_postgres),
    token: str | None = Depends(_oauth2_optional),
):
    if _sessao_valida(request.cookies.get(_AGENDAMENTO_COOKIE)):
        return SimpleNamespace(nome='ACESSO_INTERNO')
    if token:
        return valida_token_usuario_atual(session, token)
    raise HTTPException(
        status_code=401,
        detail='Acesso interno necessário. Informe a senha da clínica.',
        headers={'WWW-Authenticate': 'Bearer'},
    )


ValidaUsuarioAtual = Annotated[Usuario, Depends(valida_acesso_agendamento)]
TAMANHO_CPF = 11


def _somente_digitos(valor: str | None) -> str | None:
    digitos = ''.join(ch for ch in str(valor or '') if ch.isdigit())
    return digitos or None


def _cpf_valido(cpf: str) -> bool:
    if len(cpf) != TAMANHO_CPF or cpf == cpf[0] * TAMANHO_CPF:
        return False
    numeros = [int(ch) for ch in cpf]
    soma = sum(numeros[i] * (10 - i) for i in range(9))
    digito_1 = (soma * 10) % 11
    if digito_1 == 10:
        digito_1 = 0
    soma = sum(numeros[i] * (11 - i) for i in range(10))
    digito_2 = (soma * 10) % 11
    if digito_2 == 10:
        digito_2 = 0
    return numeros[9] == digito_1 and numeros[10] == digito_2


def _texto_maiusculo(valor: str | None, limite: int | None = None) -> str | None:
    texto = ' '.join(str(valor or '').strip().split()).upper()
    if not texto:
        return None
    return texto[:limite] if limite else texto


def _erro_oracle_resumido(exc: Exception) -> str:
    texto = ' '.join(str(exc).split())
    for marcador in ('ORA-', 'DPY-', 'DPI-'):
        posicao = texto.find(marcador)
        if posicao >= 0:
            return texto[posicao:posicao + 260]
    return texto[:260] or 'Erro nao identificado no MV.'


@router.post('/acesso/login')
def login_acesso_interno(payload: dict, response: Response):
    senha_configurada = os.getenv('AGENDAMENTO_SENHA_INTERNA', '')
    senha_informada = str(payload.get('senha') or '')
    if not senha_configurada or not hmac.compare_digest(senha_informada, senha_configurada):
        raise HTTPException(status_code=401, detail='Senha interna inválida.')
    response.set_cookie(
        _AGENDAMENTO_COOKIE,
        _sessao_assinada(int(time.time()) + _AGENDAMENTO_TTL),
        max_age=_AGENDAMENTO_TTL,
        httponly=True,
        samesite=(
            'none'
            if os.getenv('COOKIE_SECURE', 'false').lower() == 'true'
            else 'lax'
        ),
        secure=os.getenv('COOKIE_SECURE', 'false').lower() == 'true',
    )
    return {'autenticado': True, 'expira_em_segundos': _AGENDAMENTO_TTL}


@router.post('/acesso/logout')
def logout_acesso_interno(response: Response):
    response.delete_cookie(_AGENDAMENTO_COOKIE)
    return {'autenticado': False}

CONSULTA_PLANOS_ATIVOS = text(
    '''
    SELECT c.CD_CONVENIO AS cd_convenio,
           c.NM_CONVENIO AS nm_convenio,
           cp.CD_CON_PLA AS cd_con_pla,
           cp.DS_CON_PLA AS ds_con_pla
      FROM DBAMV.CONVENIO c
      JOIN DBAMV.CON_PLA cp ON cp.CD_CONVENIO = c.CD_CONVENIO
     WHERE NVL(c.SN_ATIVO, 'S') = 'S'
       AND NVL(cp.SN_ATIVO, 'S') = 'S'
       AND UPPER(TRIM(c.NM_CONVENIO)) NOT LIKE 'SUS%'
     ORDER BY c.NM_CONVENIO, cp.DS_CON_PLA
    '''
)

CONSULTA_TIPOS_MARCACAO_CONSULTA = text(
    """
    SELECT tm.CD_TIP_MAR AS cd_tip_mar,
           tm.DS_TIP_MAR AS ds_tip_mar
      FROM DBAMV.TIP_MAR tm
     WHERE tm.CD_TIP_MAR IN (1, 2, 15)
     ORDER BY CASE tm.CD_TIP_MAR
                WHEN 1 THEN 1
                WHEN 2 THEN 2
                WHEN 15 THEN 3
                WHEN 17 THEN 4
                ELSE 99
              END,
              tm.DS_TIP_MAR
    """
)

CONSULTA_DADOS_CONFIRMACAO_WHATSAPP = text(
    """
    SELECT pac.NM_PACIENTE AS nm_paciente,
           TO_CHAR(pac.NR_DDD_CELULAR) AS nr_ddd_celular,
           TO_CHAR(pac.NR_CELULAR) AS nr_celular,
           ia.DS_ITEM_AGENDAMENTO AS ds_item_agendamento,
           iac.HR_AGENDA AS horario,
           ua.DS_UNIDADE_ATENDIMENTO AS ds_unidade_atendimento
      FROM DBAMV.IT_AGENDA_CENTRAL iac
      JOIN DBAMV.PACIENTE pac
        ON pac.CD_PACIENTE = :cd_paciente
      JOIN DBAMV.ITEM_AGENDAMENTO ia
        ON ia.CD_ITEM_AGENDAMENTO = :cd_item_agendamento
      JOIN DBAMV.AGENDA_CENTRAL ac
        ON ac.CD_AGENDA_CENTRAL = iac.CD_AGENDA_CENTRAL
      LEFT JOIN DBAMV.UNIDADE_ATENDIMENTO ua
        ON ua.CD_UNIDADE_ATENDIMENTO = ac.CD_UNIDADE_ATENDIMENTO
     WHERE iac.CD_IT_AGENDA_CENTRAL = :cd_it_agenda_central
    """
)

CONSULTA_DADOS_CANCELAMENTO_WHATSAPP = text(
    """
    SELECT COALESCE(pac.NM_PACIENTE, mov.NM_PACIENTE) AS nm_paciente,
           TO_CHAR(pac.NR_DDD_CELULAR) AS nr_ddd_celular,
           TO_CHAR(pac.NR_CELULAR) AS nr_celular,
           ia.DS_ITEM_AGENDAMENTO AS ds_item_agendamento,
           COALESCE(iac.HR_AGENDA, im.HR_AGENDA) AS horario,
           ua.DS_UNIDADE_ATENDIMENTO AS ds_unidade_atendimento,
           im.CD_MOVIMENTO_AGENDA_CENTRAL AS protocolo
      FROM DBAMV.IT_AGENDA_CENTRAL iac
      LEFT JOIN DBAMV.IT_MOVIMENTO_AGENDA_CENTRAL im
        ON im.CD_IT_AGENDA_CENTRAL = iac.CD_IT_AGENDA_CENTRAL
      LEFT JOIN DBAMV.MOVIMENTO_AGENDA_CENTRAL mov
        ON mov.CD_MOVIMENTO_AGENDA_CENTRAL = im.CD_MOVIMENTO_AGENDA_CENTRAL
      LEFT JOIN DBAMV.PACIENTE pac
        ON pac.CD_PACIENTE = COALESCE(iac.CD_PACIENTE, mov.CD_PACIENTE)
      LEFT JOIN DBAMV.ITEM_AGENDAMENTO ia
        ON ia.CD_ITEM_AGENDAMENTO = COALESCE(iac.CD_ITEM_AGENDAMENTO, im.CD_ITEM_AGENDAMENTO)
      LEFT JOIN DBAMV.AGENDA_CENTRAL ac
        ON ac.CD_AGENDA_CENTRAL = COALESCE(iac.CD_AGENDA_CENTRAL, im.CD_AGENDA_CENTRAL)
      LEFT JOIN DBAMV.UNIDADE_ATENDIMENTO ua
        ON ua.CD_UNIDADE_ATENDIMENTO = COALESCE(ac.CD_UNIDADE_ATENDIMENTO, im.CD_UNIDADE_ATENDIMENTO)
     WHERE iac.CD_IT_AGENDA_CENTRAL = :cd_it_agenda_central
     ORDER BY im.CD_IT_MOVIMENTO_AGENDA_CENTRAL DESC
    """
)


def _enviar_confirmacao_whatsapp_agendamento(
    *,
    session: Session,
    payload: ConfirmarAgendamentoInput,
    protocolo: int,
) -> tuple[str | None, str | None]:
    if os.getenv('WHATSAPP_CONFIRMAR_AGENDAMENTO_AUTO', 'true').lower() != 'true':
        return 'desabilitado', 'Envio automatico de WhatsApp desabilitado.'

    dados = (
        session.execute(
            CONSULTA_DADOS_CONFIRMACAO_WHATSAPP,
            {
                'cd_paciente': payload.cd_paciente,
                'cd_item_agendamento': payload.cd_item_agendamento,
                'cd_it_agenda_central': payload.cd_it_agenda_central,
            },
        )
        .mappings()
        .first()
    )
    if not dados:
        return 'nao_enviado', 'Nao foi possivel montar os dados do WhatsApp.'

    telefone = _telefone_paciente_whatsapp(
        payload.nr_ddd_celular,
        payload.nr_celular,
        dados.get('nr_ddd_celular'),
        dados.get('nr_celular'),
    )
    if not telefone:
        return 'nao_enviado', 'Paciente sem celular para envio de WhatsApp.'

    horario = dados.get('horario')
    if isinstance(horario, datetime):
        data_texto = horario.strftime('%d/%m/%Y')
        hora_texto = horario.strftime('%H:%M')
    else:
        data_texto = ''
        hora_texto = ''

    try:
        enviar_template_whatsapp(
            telefone=telefone,
            nome_template=os.getenv(
                'WHATSAPP_TEMPLATE_CONFIRMACAO_AGENDAMENTO',
                'confirmacao_agendamento',
            ),
            idioma=os.getenv('WHATSAPP_TEMPLATE_CONFIRMACAO_IDIOMA', 'pt_BR'),
            parametros=[
                str(dados.get('nm_paciente') or 'Paciente'),
                str(dados.get('ds_item_agendamento') or 'Agendamento'),
                data_texto,
                hora_texto,
                _nome_unidade_exibicao(dados.get('ds_unidade_atendimento')),
                str(protocolo),
            ],
        )
    except HTTPException as exc:
        return 'falhou', f'WhatsApp nao enviado: {exc.detail}'
    except Exception as exc:
        return 'falhou', f'WhatsApp nao enviado: {exc}'

    return 'enviado', 'Confirmacao enviada por WhatsApp.'


def _enviar_cancelamento_whatsapp_agendamento(
    dados: dict | None,
) -> tuple[str | None, str | None]:
    if os.getenv('WHATSAPP_CANCELAR_AGENDAMENTO_AUTO', 'true').lower() != 'true':
        return 'desabilitado', 'Envio automatico de WhatsApp desabilitado.'
    if not dados:
        return 'nao_enviado', 'Nao foi possivel montar os dados do WhatsApp.'

    telefone = _telefone_paciente_whatsapp(
        None,
        None,
        dados.get('nr_ddd_celular'),
        dados.get('nr_celular'),
    )
    if not telefone:
        return 'nao_enviado', 'Paciente sem celular para envio de WhatsApp.'

    horario = dados.get('horario')
    if isinstance(horario, datetime):
        data_texto = horario.strftime('%d/%m/%Y')
        hora_texto = horario.strftime('%H:%M')
    else:
        data_texto = ''
        hora_texto = ''

    try:
        enviar_template_whatsapp(
            telefone=telefone,
            nome_template=os.getenv(
                'WHATSAPP_TEMPLATE_CANCELAMENTO_AGENDAMENTO',
                'cancelamento_agendamento_ptbr',
            ),
            idioma=os.getenv('WHATSAPP_TEMPLATE_CANCELAMENTO_IDIOMA', 'pt_BR'),
            parametros=[
                str(dados.get('nm_paciente') or 'Paciente'),
                str(dados.get('ds_item_agendamento') or 'Agendamento'),
                data_texto,
                hora_texto,
                str(dados.get('protocolo') or 'Nao informado'),
            ],
        )
    except HTTPException as exc:
        return 'falhou', f'WhatsApp nao enviado: {exc.detail}'
    except Exception as exc:
        return 'falhou', f'WhatsApp nao enviado: {exc}'

    return 'enviado', 'Cancelamento enviado por WhatsApp.'


@router.post(
    '/confirmar',
    status_code=HTTPStatus.OK,
    response_model=AgendamentoConfirmado,
)
def confirmar_agendamento(
    payload: ConfirmarAgendamentoInput,
    usuario_atual: ValidaUsuarioAtual,
    session: Session = Depends(get_session_oracle),
):
    """Conclui um agendamento usando a procedure oficial do SoulMV."""
    if os.getenv('MV_GRAVACAO_HABILITADA', 'false').lower() != 'true':
        raise HTTPException(
            status_code=HTTPStatus.NOT_IMPLEMENTED,
            detail='Gravacao do MV desabilitada neste ambiente.',
        )

    ocupado = session.execute(
        text(
            '''
            SELECT COUNT(*)
              FROM DBAMV.IT_AGENDA_CENTRAL
             WHERE CD_IT_AGENDA_CENTRAL = :slot
               AND CD_PACIENTE IS NOT NULL
            '''
        ),
        {'slot': payload.cd_it_agenda_central},
    ).scalar_one()
    if ocupado:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='Este horário já está ocupado no MV. Atualize a disponibilidade e selecione outro horário.',
        )

    slot_fim = payload.cd_it_agenda_fim or payload.cd_it_agenda_central
    connection = session.connection().connection
    cursor = connection.cursor()
    retorno = cursor.var(int)
    try:
        cursor.callproc(
            'DBAMV.PKG_AGENDAMENTO_WEB.PRC_CONCLUI_AGENDAMENTO_WEB',
            [
                payload.cd_paciente,
                payload.cd_item_agendamento,
                'A',
                payload.cd_agenda_central,
                payload.cd_it_agenda_central,
                slot_fim,
                None,
                payload.cd_tip_mar,
                retorno,
            ],
        )
        codigo = retorno.getvalue()
        if codigo != 1:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail=f'O MV recusou o agendamento (retorno {codigo}).',
            )
        row = cursor.execute(
            """
            SELECT i.CD_IT_AGENDA_CENTRAL AS horario_id,
                   i.CD_AGENDA_CENTRAL AS agenda_id,
                   im.CD_MOVIMENTO_AGENDA_CENTRAL AS movimento_id,
                   im.CD_IT_MOVIMENTO_AGENDA_CENTRAL AS item_movimento_id,
                   l.CD_LOG_OPERA_AGENDA AS log_id
              FROM DBAMV.IT_AGENDA_CENTRAL i
              LEFT JOIN DBAMV.IT_MOVIMENTO_AGENDA_CENTRAL im
                ON im.CD_IT_AGENDA_CENTRAL = i.CD_IT_AGENDA_CENTRAL
               AND im.TP_STATUS NOT IN ('E', 'C', 'P', 'T')
              LEFT JOIN DBAMV.LOG_OPERA_AGENDA_CENTRAL l
                ON l.CD_IT_AGENDA_CENTRAL = i.CD_IT_AGENDA_CENTRAL
               AND l.TP_OPERACAO = 'A'
             WHERE i.CD_IT_AGENDA_CENTRAL = :slot
             ORDER BY l.DT_OPERA_AGENDA DESC
            """,
            {'slot': payload.cd_it_agenda_central},
        ).fetchone()
        observacao = _observacao_agendamento(payload.observacao)
        if observacao:
            atual = cursor.execute(
                """
                SELECT DS_OBSERVACAO,
                       DS_OBSERVACAO_GERAL
                  FROM DBAMV.IT_AGENDA_CENTRAL
                 WHERE CD_IT_AGENDA_CENTRAL = :slot
                """,
                {'slot': payload.cd_it_agenda_central},
            ).fetchone()
            texto_novo = _anexa_observacao(atual[0] if atual else None, observacao)
            texto_geral_novo = _anexa_observacao(
                atual[1] if atual else None, observacao
            )
            cursor.execute(
                """
                UPDATE DBAMV.IT_AGENDA_CENTRAL
                   SET DS_OBSERVACAO = :observacao,
                       DS_OBSERVACAO_GERAL = :observacao_geral
                 WHERE CD_IT_AGENDA_CENTRAL = :slot
                """,
                {
                    'observacao': texto_novo,
                    'observacao_geral': texto_geral_novo,
                    'slot': payload.cd_it_agenda_central,
                },
            )
            if row is not None and row[2] is not None:
                atual_movimento = cursor.execute(
                    """
                    SELECT DS_OBSERVACAO_GERAL
                      FROM DBAMV.MOVIMENTO_AGENDA_CENTRAL
                     WHERE CD_MOVIMENTO_AGENDA_CENTRAL = :movimento
                    """,
                    {'movimento': row[2]},
                ).fetchone()
                cursor.execute(
                    """
                    UPDATE DBAMV.MOVIMENTO_AGENDA_CENTRAL
                       SET DS_OBSERVACAO_GERAL = :observacao
                     WHERE CD_MOVIMENTO_AGENDA_CENTRAL = :movimento
                    """,
                    {
                        'observacao': _anexa_observacao(
                            atual_movimento[0] if atual_movimento else None,
                            observacao,
                        ),
                        'movimento': row[2],
                    },
                )
            connection.commit()
    except HTTPException:
        raise
    except Exception as exc:
        connection.rollback()
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='Falha ao concluir o agendamento no MV.',
        ) from exc
    finally:
        cursor.close()

    if row is None or row[2] is None:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='O MV confirmou, mas nao retornou o protocolo.',
        )
    if postgres_engine is not None:
        try:
            with Session(postgres_engine) as audit_session:
                audit_session.add(
                    AuditoriaAgendamento(
                        operador_id=getattr(usuario_atual, 'id', None),
                        operador_nome=getattr(usuario_atual, 'nome', 'ACESSO_INTERNO'),
                        origem='autoatendimento' if getattr(usuario_atual, 'nome', '') == 'ACESSO_INTERNO' else 'operacional',
                        cd_paciente=payload.cd_paciente,
                        cd_item_agendamento=payload.cd_item_agendamento,
                        cd_it_agenda_central=payload.cd_it_agenda_central,
                        cd_agenda_central=payload.cd_agenda_central,
                        cd_tip_mar=payload.cd_tip_mar,
                        protocolo_mv=row[2],
                    )
                )
                audit_session.commit()
        except Exception:
            # A indisponibilidade do banco de auditoria não desfaz o agendamento já confirmado no MV.
            pass
    whatsapp_status, whatsapp_mensagem = _enviar_confirmacao_whatsapp_agendamento(
        session=session,
        payload=payload,
        protocolo=row[2],
    )
    return {
        'status': 'agendado',
        'mensagem': 'Agendamento realizado com sucesso no MV.',
        'protocolo': row[2],
        'movimento_id': row[2],
        'item_movimento_id': row[3],
        'horario_id': row[0],
        'agenda_id': row[1],
        'whatsapp_status': whatsapp_status,
        'whatsapp_mensagem': whatsapp_mensagem,
    }


@router.post(
    '/reagendar',
    status_code=HTTPStatus.OK,
    response_model=AgendamentoReagendado,
)
def reagendar_agendamento(
    payload: ReagendarAgendamentoInput,
    usuario_atual: ValidaUsuarioAtual,
    session: Session = Depends(get_session_oracle),
):
    """Troca o horario em uma unica transacao no SoulMV."""
    del usuario_atual
    if os.getenv('MV_GRAVACAO_HABILITADA', 'false').lower() != 'true':
        raise HTTPException(
            status_code=HTTPStatus.NOT_IMPLEMENTED,
            detail='Gravacao do MV desabilitada neste ambiente.',
        )
    if payload.cd_it_agenda_central_anterior == payload.cd_it_agenda_central:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='Selecione um horario diferente do agendamento atual.',
        )

    anterior = session.execute(
        text(
            '''
            SELECT CD_PACIENTE, CD_ITEM_AGENDAMENTO
              FROM DBAMV.IT_AGENDA_CENTRAL
             WHERE CD_IT_AGENDA_CENTRAL = :slot
            '''
        ),
        {'slot': payload.cd_it_agenda_central_anterior},
    ).first()
    if anterior is None or anterior[0] != payload.cd_paciente:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='O agendamento atual nao foi encontrado para este paciente.',
        )
    if anterior[1] != payload.cd_item_agendamento:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='O item do reagendamento difere do agendamento atual.',
        )

    novo = session.execute(
        text(
            '''
            SELECT CD_AGENDA_CENTRAL, CD_ITEM_AGENDAMENTO, CD_PACIENTE,
                   NVL(SN_BLOQUEADO, 'N')
              FROM DBAMV.IT_AGENDA_CENTRAL
             WHERE CD_IT_AGENDA_CENTRAL = :slot
            '''
        ),
        {'slot': payload.cd_it_agenda_central},
    ).first()
    if novo is None or novo[1] != payload.cd_item_agendamento:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='O novo horario nao pertence ao mesmo item de agendamento.',
        )
    if novo[2] is not None or novo[3] == 'S':
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='O novo horario nao esta mais disponivel no MV.',
        )

    connection = session.connection().connection
    cursor = connection.cursor()
    retorno_cancelamento = cursor.var(str, 10)
    retorno_confirmacao = cursor.var(int)
    row = None
    try:
        cursor.callproc(
            'DBAMV.PKG_AGENDAMENTO_WEB.PRC_EXCLUIR_AGD_WEB',
            [
                payload.cd_it_agenda_central_anterior,
                payload.motivo,
                retorno_cancelamento,
            ],
        )
        codigo_cancelamento = retorno_cancelamento.getvalue()
        if codigo_cancelamento not in ('S', '1', 'OK', None):
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail=(
                    'O MV recusou o cancelamento do horario anterior '
                    f'(retorno {codigo_cancelamento}).'
                ),
            )

        cursor.callproc(
            'DBAMV.PKG_AGENDAMENTO_WEB.PRC_CONCLUI_AGENDAMENTO_WEB',
            [
                payload.cd_paciente,
                payload.cd_item_agendamento,
                'A',
                novo[0],
                payload.cd_it_agenda_central,
                payload.cd_it_agenda_fim or payload.cd_it_agenda_central,
                None,
                payload.cd_tip_mar,
                retorno_confirmacao,
            ],
        )
        if retorno_confirmacao.getvalue() != 1:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail=(
                    'O MV recusou o novo horario. '
                    'O agendamento anterior foi preservado.'
                ),
            )
        row = cursor.execute(
            '''
            SELECT i.CD_IT_AGENDA_CENTRAL,
                   i.CD_AGENDA_CENTRAL,
                   im.CD_MOVIMENTO_AGENDA_CENTRAL,
                   im.CD_IT_MOVIMENTO_AGENDA_CENTRAL
              FROM DBAMV.IT_AGENDA_CENTRAL i
              LEFT JOIN DBAMV.IT_MOVIMENTO_AGENDA_CENTRAL im
                ON im.CD_IT_AGENDA_CENTRAL = i.CD_IT_AGENDA_CENTRAL
               AND im.TP_STATUS NOT IN ('E', 'C', 'P', 'T')
             WHERE i.CD_IT_AGENDA_CENTRAL = :slot
             ORDER BY im.CD_IT_MOVIMENTO_AGENDA_CENTRAL DESC
            ''',
            {'slot': payload.cd_it_agenda_central},
        ).fetchone()
        if row is None or row[2] is None:
            raise HTTPException(
                status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                detail=(
                    'O MV nao retornou o protocolo do novo horario. '
                    'O agendamento anterior foi preservado.'
                ),
            )
        connection.commit()
    except HTTPException:
        connection.rollback()
        raise
    except Exception as exc:
        connection.rollback()
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail=(
                'Nao foi possivel concluir o reagendamento. '
                'O horario anterior foi preservado.'
            ),
        ) from exc
    finally:
        cursor.close()

    whatsapp_status, whatsapp_mensagem = _enviar_confirmacao_whatsapp_agendamento(
        session=session,
        payload=payload,
        protocolo=row[2],
    )
    return {
        'status': 'reagendado',
        'mensagem': 'Agendamento alterado com sucesso no MV.',
        'protocolo': row[2],
        'movimento_id': row[2],
        'item_movimento_id': row[3],
        'horario_id': row[0],
        'agenda_id': row[1],
        'horario_anterior_id': payload.cd_it_agenda_central_anterior,
        'whatsapp_status': whatsapp_status,
        'whatsapp_mensagem': whatsapp_mensagem,
    }


CONSULTA_PACIENTES = text(
    """
    SELECT *
      FROM (
        SELECT p.CD_PACIENTE AS cd_paciente,
               p.NM_PACIENTE AS nm_paciente,
               p.DT_NASCIMENTO AS dt_nascimento,
               p.TP_SEXO AS tp_sexo,
               p.NR_CPF AS nr_cpf,
               p.EMAIL AS email,
               TO_CHAR(p.NR_DDD_CELULAR) AS nr_ddd_celular,
               TO_CHAR(p.NR_CELULAR) AS nr_celular
          FROM DBAMV.PACIENTE p
         WHERE p.CD_PACIENTE = :codigo
            OR REGEXP_REPLACE(p.NR_CPF, '[^0-9]', '') = :cpf
            OR UPPER(p.NM_PACIENTE) LIKE :nome
         ORDER BY p.NM_PACIENTE, p.CD_PACIENTE
      )
     WHERE ROWNUM <= :limite
    """
)

CONSULTA_ULTIMO_ATENDIMENTO_PACIENTE = text(
    """
    SELECT *
      FROM (
        SELECT a.CD_ATENDIMENTO AS cd_atendimento,
               a.HR_ATENDIMENTO AS horario_atendimento,
               a.TP_ATENDIMENTO AS tipo_atendimento,
               CASE a.TP_ATENDIMENTO
                 WHEN 'A' THEN 'Ambulatorial'
                 WHEN 'E' THEN 'Externo'
                 WHEN 'U' THEN 'Urgência'
                 WHEN 'I' THEN 'Internação'
                 ELSE a.TP_ATENDIMENTO
               END AS ds_tipo_atendimento,
               a.CD_PRESTADOR AS cd_prestador,
               pr.NM_PRESTADOR AS nm_prestador,
               a.CD_CONVENIO AS cd_convenio,
               c.NM_CONVENIO AS nm_convenio,
               a.CD_CON_PLA AS cd_con_pla,
               cp.DS_CON_PLA AS ds_con_pla
          FROM DBAMV.ATENDIME a
          LEFT JOIN DBAMV.PRESTADOR pr
            ON pr.CD_PRESTADOR = a.CD_PRESTADOR
          LEFT JOIN DBAMV.CONVENIO c
            ON c.CD_CONVENIO = a.CD_CONVENIO
          LEFT JOIN DBAMV.CON_PLA cp
            ON cp.CD_CONVENIO = a.CD_CONVENIO
           AND cp.CD_CON_PLA = a.CD_CON_PLA
         WHERE a.CD_PACIENTE = :cd_paciente
           AND a.HR_ATENDIMENTO IS NOT NULL
         ORDER BY a.HR_ATENDIMENTO DESC, a.CD_ATENDIMENTO DESC
      )
     WHERE ROWNUM = 1
    """
)

CONSULTA_LINHAS_CUIDADO_PACIENTE = text(
    """
    SELECT *
      FROM (
        SELECT v.CD_ATENDIMENTO AS cd_atendimento,
               v.CD_PACIENTE AS cd_paciente,
               v.CD_DOCUMENTO AS cd_documento,
               v.CD_REGISTRO AS cd_registro,
               v.DS_TIPO_DOCUMENTO AS ds_tipo_documento,
               v.DS_DOCUMENTO AS ds_documento,
               v.TP_STATUS AS tp_status,
               v.DS_CAMPO_FILHO AS ds_campo_filho,
               v.DS_IDENTIFICADOR_FILHO AS ds_identificador_filho,
               v.DS_RESPOSTA AS ds_resposta,
               v.DH_DOCUMENTO AS dh_documento,
               v.DH_FECHAMENTO AS dh_fechamento,
               v.CD_USUARIO_CRIOU AS cd_usuario_criou
          FROM DBAMV.VDIC_PW_RESPOSTA_DOCUMENTO v
         WHERE v.CD_PACIENTE = :cd_paciente
           AND v.CD_DOCUMENTO = 1043
           AND UPPER(v.DS_DOCUMENTO) = 'LC_DAC'
           AND v.TP_STATUS = 'FECHADO'
         ORDER BY v.DH_DOCUMENTO DESC NULLS LAST,
                  v.CD_REGISTRO DESC NULLS LAST,
                  v.CD_CAMPO_FILHO
      )
     WHERE ROWNUM <= 250
    """
)

CONSULTA_ITENS = text(
    """
    SELECT *
      FROM (
        SELECT DISTINCT
               ia.CD_ITEM_AGENDAMENTO AS cd_item_agendamento,
               ia.DS_ITEM_AGENDAMENTO AS ds_item_agendamento,
               ia.CD_EXA_RX AS cd_exa_rx,
               ia.HR_REALIZACAO AS hr_realizacao
          FROM DBAMV.ITEM_AGENDAMENTO ia
          JOIN DBAMV.AGENDA_CENTRAL_ITEM_AGENDA acia
            ON acia.CD_ITEM_AGENDAMENTO = ia.CD_ITEM_AGENDAMENTO
          JOIN DBAMV.AGENDA_CENTRAL ac
            ON ac.CD_AGENDA_CENTRAL = acia.CD_AGENDA_CENTRAL
         WHERE (ia.CD_ITEM_AGENDAMENTO = :codigo
            OR UPPER(ia.DS_ITEM_AGENDAMENTO) LIKE :descricao)
           AND ac.DT_AGENDA >= TRUNC(SYSDATE)
           AND ac.DT_LIBERACAO < SYSDATE
           AND NVL(ac.QT_MARCADOS, 0) < ac.QT_ATENDIMENTO
         ORDER BY ia.DS_ITEM_AGENDAMENTO, ia.CD_ITEM_AGENDAMENTO
      )
     WHERE ROWNUM <= :limite
    """
)

CONSULTA_CONVENIOS_PACIENTE = text(
    """
    SELECT cd_convenio, nm_convenio, cd_con_pla, ds_con_pla,
           ultimo_atendimento
      FROM (
        SELECT x.*,
               ROW_NUMBER() OVER (
                   PARTITION BY x.cd_convenio, x.cd_con_pla
                   ORDER BY x.ultimo_atendimento DESC NULLS LAST
               ) AS ordem
          FROM (
            SELECT c.CD_CONVENIO AS cd_convenio,
                   c.NM_CONVENIO AS nm_convenio,
                   cp.CD_CON_PLA AS cd_con_pla,
                   cp.DS_CON_PLA AS ds_con_pla,
                   a.HR_ATENDIMENTO AS ultimo_atendimento
              FROM DBAMV.ATENDIME a
              JOIN DBAMV.CONVENIO c ON c.CD_CONVENIO = a.CD_CONVENIO
              JOIN DBAMV.CON_PLA cp
                ON cp.CD_CONVENIO = a.CD_CONVENIO
               AND cp.CD_CON_PLA = a.CD_CON_PLA
             WHERE a.CD_PACIENTE = :cd_paciente
            UNION ALL
            SELECT c.CD_CONVENIO, c.NM_CONVENIO, cp.CD_CON_PLA,
                   cp.DS_CON_PLA, NULL
              FROM DBAMV.CARTEIRA ca
              JOIN DBAMV.CONVENIO c ON c.CD_CONVENIO = ca.CD_CONVENIO
              JOIN DBAMV.CON_PLA cp
                ON cp.CD_CONVENIO = ca.CD_CONVENIO
               AND cp.CD_CON_PLA = ca.CD_CON_PLA
             WHERE ca.CD_PACIENTE = :cd_paciente
               AND NVL(ca.SN_CARTEIRA_ATIVO, 'S') = 'S'
          ) x
      )
     WHERE ordem = 1
     ORDER BY ultimo_atendimento DESC, nm_convenio, ds_con_pla
    """
)

CONSULTA_PRESTADORES_ITEM = text(
    """
    SELECT *
      FROM (
        SELECT DISTINCT
               p.CD_PRESTADOR AS cd_prestador,
               p.NM_PRESTADOR AS nm_prestador,
               p.DS_CODIGO_CONSELHO AS ds_codigo_conselho
          FROM DBAMV.AGENDA_CENTRAL_ITEM_AGENDA acia
          JOIN DBAMV.AGENDA_CENTRAL ac
            ON ac.CD_AGENDA_CENTRAL = acia.CD_AGENDA_CENTRAL
          JOIN DBAMV.IT_AGENDA_CENTRAL iac
            ON iac.CD_AGENDA_CENTRAL = ac.CD_AGENDA_CENTRAL
          JOIN DBAMV.PRESTADOR p
            ON p.CD_PRESTADOR = ac.CD_PRESTADOR
         WHERE acia.CD_ITEM_AGENDAMENTO = :cd_item_agendamento
           AND p.TP_SITUACAO = 'A'
           AND ac.DT_AGENDA BETWEEN :data_inicio AND :data_fim
           AND ac.DT_LIBERACAO < SYSDATE
           AND NVL(ac.QT_MARCADOS, 0) < ac.QT_ATENDIMENTO
           AND iac.HR_AGENDA >= SYSDATE
           AND iac.CD_PACIENTE IS NULL
           AND iac.DT_GRAVACAO IS NULL
           AND NVL(iac.SN_BLOQUEADO, 'N') <> 'S'
         ORDER BY p.NM_PRESTADOR, p.CD_PRESTADOR
      )
     WHERE ROWNUM <= :limite
    """
)

CONSULTA_ITEM_EXIGE_PRESTADOR = text(
    """
    SELECT COUNT(*)
      FROM (
        SELECT iap.CD_PRESTADOR
          FROM DBAMV.ITEM_AGENDAMENTO_PRESTADOR iap
         WHERE iap.CD_ITEM_AGENDAMENTO = :cd_item_agendamento
           AND ROWNUM = 1
        UNION ALL
        SELECT ac.CD_PRESTADOR
          FROM DBAMV.AGENDA_CENTRAL_ITEM_AGENDA acia
          JOIN DBAMV.AGENDA_CENTRAL ac
            ON ac.CD_AGENDA_CENTRAL = acia.CD_AGENDA_CENTRAL
         WHERE acia.CD_ITEM_AGENDAMENTO = :cd_item_agendamento
           AND ac.CD_PRESTADOR IS NOT NULL
           AND ROWNUM = 1
      )
    """
)

CONSULTA_PRE_VALIDACAO = text(
    """
    SELECT iac.CD_IT_AGENDA_CENTRAL AS cd_it_agenda_central,
           iac.CD_AGENDA_CENTRAL AS cd_agenda_central,
           iac.CD_TIP_MAR AS cd_tip_mar,
           iac.HR_AGENDA AS horario,
           iac.CD_PACIENTE AS slot_cd_paciente,
           iac.DT_GRAVACAO AS slot_dt_gravacao,
           NVL(iac.SN_BLOQUEADO, 'N') AS slot_bloqueado,
           pac.CD_PACIENTE AS cd_paciente,
           pac.NM_PACIENTE AS nm_paciente,
           ia.CD_ITEM_AGENDAMENTO AS cd_item_agendamento,
           ia.DS_ITEM_AGENDAMENTO AS ds_item_agendamento,
           c.CD_CONVENIO AS cd_convenio,
           c.NM_CONVENIO AS nm_convenio,
           cp.CD_CON_PLA AS cd_con_pla,
           cp.DS_CON_PLA AS ds_con_pla,
           ac.CD_PRESTADOR AS cd_prestador,
           p.NM_PRESTADOR AS nm_prestador,
           ua.DS_UNIDADE_ATENDIMENTO AS ds_unidade_atendimento,
           ua.DS_LOCAL_UNIDADE_ATENDIMENTO
               AS ds_local_unidade_atendimento
      FROM DBAMV.IT_AGENDA_CENTRAL iac
      JOIN DBAMV.AGENDA_CENTRAL ac
        ON ac.CD_AGENDA_CENTRAL = iac.CD_AGENDA_CENTRAL
      JOIN DBAMV.AGENDA_CENTRAL_ITEM_AGENDA acia
        ON acia.CD_AGENDA_CENTRAL = ac.CD_AGENDA_CENTRAL
       AND acia.CD_ITEM_AGENDAMENTO = :cd_item_agendamento
      JOIN DBAMV.ITEM_AGENDAMENTO ia
        ON ia.CD_ITEM_AGENDAMENTO = acia.CD_ITEM_AGENDAMENTO
      JOIN DBAMV.PACIENTE pac
        ON pac.CD_PACIENTE = :cd_paciente
      JOIN DBAMV.CONVENIO c
        ON c.CD_CONVENIO = :cd_convenio
      JOIN DBAMV.CON_PLA cp
        ON cp.CD_CONVENIO = c.CD_CONVENIO
       AND cp.CD_CON_PLA = :cd_con_pla
      LEFT JOIN DBAMV.PRESTADOR p
        ON p.CD_PRESTADOR = ac.CD_PRESTADOR
      LEFT JOIN DBAMV.UNIDADE_ATENDIMENTO ua
        ON ua.CD_UNIDADE_ATENDIMENTO = ac.CD_UNIDADE_ATENDIMENTO
     WHERE iac.CD_IT_AGENDA_CENTRAL = :cd_it_agenda_central
    """
)

CONSULTA_AGENDAMENTO_DUPLICADO = text(
    """
    SELECT *
      FROM (
        SELECT iac.CD_IT_AGENDA_CENTRAL AS cd_it_agenda_central,
               iac.HR_AGENDA AS horario
          FROM DBAMV.IT_AGENDA_CENTRAL iac
         WHERE iac.CD_PACIENTE = :cd_paciente
           AND iac.CD_ITEM_AGENDAMENTO = :cd_item_agendamento
           AND iac.HR_AGENDA >= SYSDATE
         ORDER BY iac.HR_AGENDA
      )
     WHERE ROWNUM = 1
    """
)

CONSULTA_AGENDAMENTOS_PACIENTE = text(
    """
    SELECT im.CD_MOVIMENTO_AGENDA_CENTRAL AS protocolo,
           im.CD_IT_MOVIMENTO_AGENDA_CENTRAL AS item_movimento_id,
           i.CD_IT_AGENDA_CENTRAL AS cd_it_agenda_central,
           i.CD_AGENDA_CENTRAL AS cd_agenda_central,
           i.CD_ITEM_AGENDAMENTO AS cd_item_agendamento,
           ia.DS_ITEM_AGENDAMENTO AS ds_item_agendamento,
           i.HR_AGENDA AS horario,
           ac.CD_PRESTADOR AS cd_prestador,
           p.NM_PRESTADOR AS nm_prestador,
           ua.DS_UNIDADE_ATENDIMENTO AS ds_unidade_atendimento,
           im.TP_STATUS AS status,
           i.CD_TIP_MAR AS cd_tip_mar,
           i.CD_CONVENIO AS cd_convenio,
           c.NM_CONVENIO AS nm_convenio,
           i.CD_CON_PLA AS cd_con_pla,
           cp.DS_CON_PLA AS ds_con_pla
      FROM DBAMV.IT_AGENDA_CENTRAL i
      JOIN DBAMV.AGENDA_CENTRAL ac
        ON ac.CD_AGENDA_CENTRAL = i.CD_AGENDA_CENTRAL
      JOIN DBAMV.ITEM_AGENDAMENTO ia
        ON ia.CD_ITEM_AGENDAMENTO = i.CD_ITEM_AGENDAMENTO
      LEFT JOIN DBAMV.IT_MOVIMENTO_AGENDA_CENTRAL im
        ON im.CD_IT_AGENDA_CENTRAL = i.CD_IT_AGENDA_CENTRAL
       AND im.TP_STATUS NOT IN ('E', 'C', 'P', 'T')
      LEFT JOIN DBAMV.PRESTADOR p ON p.CD_PRESTADOR = ac.CD_PRESTADOR
      LEFT JOIN DBAMV.UNIDADE_ATENDIMENTO ua
        ON ua.CD_UNIDADE_ATENDIMENTO = ac.CD_UNIDADE_ATENDIMENTO
      LEFT JOIN DBAMV.CONVENIO c
        ON c.CD_CONVENIO = i.CD_CONVENIO
      LEFT JOIN DBAMV.CON_PLA cp
        ON cp.CD_CONVENIO = i.CD_CONVENIO
       AND cp.CD_CON_PLA = i.CD_CON_PLA
     WHERE i.CD_PACIENTE = :cd_paciente
       AND i.HR_AGENDA >= SYSDATE
     ORDER BY i.HR_AGENDA
    """
)

CONSULTA_HORARIOS = text(
    """
    SELECT *
      FROM (
        SELECT iac.CD_IT_AGENDA_CENTRAL AS cd_it_agenda_central,
               iac.CD_AGENDA_CENTRAL AS cd_agenda_central,
               iac.CD_TIP_MAR AS cd_tip_mar,
               ia.CD_ITEM_AGENDAMENTO AS cd_item_agendamento,
               ia.DS_ITEM_AGENDAMENTO AS ds_item_agendamento,
               ac.DT_AGENDA AS data_agenda,
               iac.HR_AGENDA AS horario,
               ac.CD_UNIDADE_ATENDIMENTO AS cd_unidade_atendimento,
               ua.DS_UNIDADE_ATENDIMENTO AS ds_unidade_atendimento,
               ua.DS_LOCAL_UNIDADE_ATENDIMENTO
                   AS ds_local_unidade_atendimento,
               ac.CD_PRESTADOR AS cd_prestador,
               p.NM_PRESTADOR AS nm_prestador
          FROM DBAMV.IT_AGENDA_CENTRAL iac
          JOIN DBAMV.AGENDA_CENTRAL ac
            ON ac.CD_AGENDA_CENTRAL = iac.CD_AGENDA_CENTRAL
          JOIN DBAMV.AGENDA_CENTRAL_ITEM_AGENDA acia
            ON acia.CD_AGENDA_CENTRAL = ac.CD_AGENDA_CENTRAL
          JOIN DBAMV.ITEM_AGENDAMENTO ia
            ON ia.CD_ITEM_AGENDAMENTO = acia.CD_ITEM_AGENDAMENTO
          LEFT JOIN DBAMV.UNIDADE_ATENDIMENTO ua
            ON ua.CD_UNIDADE_ATENDIMENTO = ac.CD_UNIDADE_ATENDIMENTO
          LEFT JOIN DBAMV.PRESTADOR p
            ON p.CD_PRESTADOR = ac.CD_PRESTADOR
         WHERE ia.CD_ITEM_AGENDAMENTO = :cd_item_agendamento
           AND ac.DT_AGENDA BETWEEN :data_inicio AND :data_fim
           AND iac.HR_AGENDA >= SYSDATE
           AND iac.CD_PACIENTE IS NULL
           AND iac.DT_GRAVACAO IS NULL
           AND NVL(iac.SN_BLOQUEADO, 'N') <> 'S'
           AND ac.DT_LIBERACAO < SYSDATE
           AND NVL(ac.QT_MARCADOS, 0) < ac.QT_ATENDIMENTO
           AND (:cd_prestador IS NULL
                OR ac.CD_PRESTADOR = :cd_prestador)
         ORDER BY ac.DT_AGENDA, iac.HR_AGENDA
      )
     WHERE ROWNUM <= :limite
    """
)


def _cpf_final(cpf: str | None) -> str | None:
    if not cpf:
        return None
    digitos = ''.join(caractere for caractere in cpf if caractere.isdigit())
    return digitos[-4:] if digitos else None


@router.get(
    '/planos-ativos',
    status_code=HTTPStatus.OK,
    response_model=PlanosDisponiveis,
)
def consultar_planos_ativos(
    usuario_atual: ValidaUsuarioAtual,
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    try:
        rows = session.execute(CONSULTA_PLANOS_ATIVOS).mappings().all()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel consultar os planos no MV.',
        ) from exc
    return {'planos': [dict(row) for row in rows], 'total': len(rows)}


@router.get(
    '/tipos-consulta',
    status_code=HTTPStatus.OK,
    response_model=TiposMarcacaoConsulta,
)
def consultar_tipos_consulta(
    usuario_atual: ValidaUsuarioAtual,
    session: Session = Depends(get_session_oracle),
):
    """Retorna os tipos de marcação de consulta cadastrados no MV."""
    del usuario_atual
    try:
        rows = session.execute(CONSULTA_TIPOS_MARCACAO_CONSULTA).mappings().all()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel consultar os tipos de consulta no MV.',
        ) from exc
    return {'tipos': [dict(row) for row in rows], 'total': len(rows)}


@router.get(
    '/pacientes',
    status_code=HTTPStatus.OK,
    response_model=PacientesEncontrados,
)
def consultar_pacientes(
    usuario_atual: ValidaUsuarioAtual,
    termo: Annotated[str, Query(min_length=3, max_length=80)],
    limite: Annotated[int, Query(ge=1, le=20)] = 10,
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    termo_limpo = termo.strip()
    digitos = ''.join(
        caractere for caractere in termo_limpo if caractere.isdigit()
    )
    codigo = int(termo_limpo) if termo_limpo.isdigit() else -1
    cpf = digitos if len(digitos) == TAMANHO_CPF else '-1'

    try:
        rows = (
            session
            .execute(
                CONSULTA_PACIENTES,
                {
                    'codigo': codigo,
                    'cpf': cpf,
                    'nome': f'%{termo_limpo.upper()}%',
                    'limite': limite,
                },
            )
            .mappings()
            .all()
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel consultar os pacientes no MV.',
        ) from exc

    pacientes = []
    for row in rows:
        paciente = dict(row)
        nascimento = paciente.get('dt_nascimento')
        if isinstance(nascimento, datetime):
            paciente['dt_nascimento'] = nascimento.date()
        paciente['cpf_final'] = _cpf_final(paciente.pop('nr_cpf', None))
        pacientes.append(paciente)
    return {'pacientes': pacientes, 'total': len(pacientes)}


@router.post(
    '/pacientes/cadastrar',
    status_code=HTTPStatus.CREATED,
    response_model=PacienteCadastrado,
)
def cadastrar_paciente(
    payload: CadastroPacienteInput,
    usuario_atual: ValidaUsuarioAtual,
    session: Session = Depends(get_session_oracle),
):
    """Cadastra um paciente novo e seus dados de contato/endereco no MV.

    A rota fica separada da busca para que o CPF seja revalidado dentro da
    mesma transacao antes do INSERT. A habilitacao deve ser explicita no
    ambiente (`MV_CADASTRO_PACIENTE_HABILITADO=true`).
    """
    if os.getenv('MV_CADASTRO_PACIENTE_HABILITADO', 'false').lower() != 'true':
        raise HTTPException(
            status_code=HTTPStatus.NOT_IMPLEMENTED,
            detail='Cadastro de paciente desabilitado neste ambiente.',
        )
    cpf = _somente_digitos(payload.nr_cpf) or ''
    if len(cpf) != TAMANHO_CPF:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='CPF deve conter 11 digitos.',
        )
    if not _cpf_valido(cpf):
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='CPF invalido. Confira os numeros antes de cadastrar.',
        )
    cep = _somente_digitos(payload.nr_cep)
    if cep and len(cep) != 8:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='CEP deve conter 8 digitos.',
        )
    ddd_celular = _somente_digitos(payload.nr_ddd_celular)
    celular = _somente_digitos(payload.nr_celular)
    if celular and not ddd_celular:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='Informe o DDD do celular.',
        )
    nr_carteira = ' '.join(str(payload.nr_carteira or '').strip().split()) or None
    email = payload.email.strip().lower() if payload.email else None
    nome_paciente = _texto_maiusculo(payload.nm_paciente, 200)
    ds_endereco = _texto_maiusculo(payload.ds_endereco, 200)
    ds_complemento = _texto_maiusculo(payload.ds_complemento, 100)
    nm_bairro = _texto_maiusculo(payload.nm_bairro, 100)
    connection = session.connection().connection
    cursor = connection.cursor()
    try:
        # O MV mantém a empresa em contexto de sessão; sem isso o trigger de
        # integração do cadastro não encontra CONFIG_MVINTEGRA.
        cursor.callproc('DBAMV.PKG_MV2000.ATRIBUI_EMPRESA', [1])
        existente = cursor.execute(
            """
            SELECT CD_PACIENTE, NM_PACIENTE
              FROM DBAMV.PACIENTE
             WHERE REGEXP_REPLACE(NR_CPF, '[^0-9]', '') = :cpf
             FETCH FIRST 1 ROW ONLY
            """,
            {'cpf': cpf},
        ).fetchone()
        plano = cursor.execute(
            """
            SELECT 1
              FROM DBAMV.CONVENIO c
              JOIN DBAMV.CON_PLA cp ON cp.CD_CONVENIO = c.CD_CONVENIO
             WHERE c.CD_CONVENIO = :cd_convenio
               AND cp.CD_CON_PLA = :cd_con_pla
               AND NVL(c.SN_ATIVO, 'S') = 'S'
               AND NVL(cp.SN_ATIVO, 'S') = 'S'
            """,
            {
                'cd_convenio': payload.cd_convenio,
                'cd_con_pla': payload.cd_con_pla,
            },
        ).fetchone()
        if not plano:
            raise HTTPException(
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                detail='Convenio/plano invalido ou inativo no MV.',
            )
        if existente:
            paciente_id = int(existente[0])
            nome_existente = (
                str(existente[1] or payload.nm_paciente).strip().upper()
            )
            carteira = cursor.execute(
                """
                SELECT 1
                  FROM DBAMV.CARTEIRA
                 WHERE CD_PACIENTE = :paciente
                   AND CD_CONVENIO = :cd_convenio
                   AND CD_CON_PLA = :cd_con_pla
                   AND NVL(SN_CARTEIRA_ATIVO, 'S') = 'S'
                 FETCH FIRST 1 ROW ONLY
                """,
                {
                    'paciente': paciente_id,
                    'cd_convenio': payload.cd_convenio,
                    'cd_con_pla': payload.cd_con_pla,
                },
            ).fetchone()
            if not carteira:
                cursor.execute(
                    """
                    INSERT INTO DBAMV.CARTEIRA (
                        CD_CONVENIO, CD_PACIENTE, CD_CON_PLA, NR_CARTEIRA,
                        NM_TITULAR, SN_TITULAR, SN_CARTEIRA_ATIVO
                    ) VALUES (
                        :cd_convenio, :paciente, :cd_con_pla, :nr_carteira,
                        :nm_titular, 'S', 'S'
                    )
                    """,
                    {
                        'cd_convenio': payload.cd_convenio,
                        'paciente': paciente_id,
                        'cd_con_pla': payload.cd_con_pla,
                        'nr_carteira': nr_carteira,
                        'nm_titular': nome_existente,
                    },
                )
            connection.commit()
            return {
                'cd_paciente': paciente_id,
                'nm_paciente': nome_existente,
                'nr_cpf': cpf,
                'mensagem': (
                    'Paciente ja existia no MV. Convenio/plano garantido '
                    'para seguir o agendamento.'
                ),
            }
        operador = getattr(usuario_atual, 'nome', None) or 'API_PRONTOCARDIO'
        cursor.execute(
            """
            INSERT INTO DBAMV.PACIENTE (
                CD_PACIENTE, TP_SITUACAO, SN_ALT_DADOS_ORA_APP,
                SN_RECEBE_CONTATO, SN_VIP, SN_NOTIFICACAO_SMS,
                DT_CADASTRO, DT_CADASTRO_MANUAL, SN_ENDERECO_SEM_NUMERO,
                SN_RUT_FICTICIO, SN_ONCOLOGICO, NM_PACIENTE, NR_CPF,
                DT_NASCIMENTO, TP_SEXO, EMAIL, NR_DDD_CELULAR, NR_CELULAR,
                DS_ENDERECO, NR_ENDERECO, DS_COMPLEMENTO, NM_BAIRRO,
                NR_CEP, CD_CIDADE, NM_USUARIO, CD_MULTI_EMPRESA
            ) VALUES (
                SEQ_PACIENTE.NEXTVAL, 'N', 'S', 'N', 'N', 'N', SYSDATE,
                SYSDATE, 'N', 'N', 'N', :nm_paciente, :cpf,
                TO_DATE(:dt_nascimento, 'YYYY-MM-DD'), :tp_sexo, :email,
                :nr_ddd_celular, :nr_celular, :ds_endereco, :nr_endereco,
                :ds_complemento, :nm_bairro, :nr_cep, :cd_cidade,
                :nm_usuario, 1
            )
            """,
            {
                'nm_paciente': nome_paciente,
                'cpf': cpf,
                'dt_nascimento': payload.dt_nascimento.isoformat(),
                'tp_sexo': payload.tp_sexo,
                'email': email,
                'nr_ddd_celular': ddd_celular,
                'nr_celular': celular,
                'ds_endereco': ds_endereco,
                'nr_endereco': payload.nr_endereco,
                'ds_complemento': ds_complemento,
                'nm_bairro': nm_bairro,
                'nr_cep': cep,
                'cd_cidade': payload.cd_cidade,
                'nm_usuario': operador[:30],
            },
        )
        paciente_id = cursor.execute(
            'SELECT SEQ_PACIENTE.CURRVAL FROM DUAL'
        ).fetchone()[0]
        if ds_endereco or cep or payload.cd_cidade:
            cursor.execute(
                """
                INSERT INTO DBAMV.ENDERECO_PACIENTE (
                    CD_ENDERECO_PACIENTE, CD_PACIENTE, NR_CEP,
                    DS_ENDERECO, NR_ENDERECO, DS_COMPLEMENTO, NM_BAIRRO,
                    CD_CIDADE, TP_ENDERECO, SN_PADRAO, SN_ENDERECO_EXTERNO
                ) VALUES (
                    SEQ_ENDERECO_PACIENTE.NEXTVAL, :paciente, :nr_cep,
                    :ds_endereco, :nr_endereco, :ds_complemento, :nm_bairro,
                    :cd_cidade, 'R', 'S', 'N'
                )
                """,
                {
                    'paciente': paciente_id,
                    'nr_cep': cep,
                    'ds_endereco': ds_endereco,
                    'nr_endereco': payload.nr_endereco,
                    'ds_complemento': ds_complemento,
                    'nm_bairro': nm_bairro,
                    'cd_cidade': payload.cd_cidade,
                },
            )
            cursor.execute(
                """
                INSERT INTO DBAMV.ENDERECO (
                    CD_ENDERECO, CD_PACIENTE, DS_ENDERECO, NR_ENDERECO,
                    NR_FONE, DS_COMPLEMENTO, NM_BAIRRO, NR_CEP, SN_PADRAO
                ) VALUES (
                    SEQ_ENDERECO.NEXTVAL, :paciente, :ds_endereco,
                    :nr_endereco, :nr_celular, :ds_complemento, :nm_bairro,
                    :nr_cep, 'S'
                )
                """,
                {
                    'paciente': paciente_id,
                    'nr_cep': cep,
                    'ds_endereco': ds_endereco,
                    'nr_endereco': payload.nr_endereco,
                    'ds_complemento': ds_complemento,
                    'nm_bairro': nm_bairro,
                    'nr_celular': celular,
                },
            )
        if celular:
            cursor.execute(
                """
                INSERT INTO DBAMV.CONTATO_PACIENTE (
                    CD_CONTATO_PACIENTE, CD_PACIENTE, NR_DDD, NR_TELEFONE,
                    DS_TIP_COMUN, TP_CONTATO, SN_PADRAO, SN_SMS
                ) VALUES (
                    SEQ_CONTATO_PACIENTE.NEXTVAL, :paciente, :ddd, :telefone,
                    'CELULAR', 'C', 'S', 'N'
                )
                """,
                {
                    'paciente': paciente_id,
                    'ddd': ddd_celular,
                    'telefone': celular,
                },
            )
        if email:
            cursor.execute(
                """
                INSERT INTO DBAMV.OUTROS_CONTATOS_PACIENTE (
                    CD_OUTROS_CONTATOS_PACIENTE, CD_PACIENTE,
                    TP_OUTROS_CONTATO, CONTATO, SN_PADRAO,
                    SN_RECEBE_CONTATO, DS_TIP_COMUN
                ) VALUES (
                    SEQ_OUTROS_CONTATOS_PACIENTE.NEXTVAL, :paciente,
                    'E', :email, 'S', 'N', 'E-MAIL'
                )
                """,
                {'paciente': paciente_id, 'email': email},
            )
        cursor.execute(
            """
            INSERT INTO DBAMV.CARTEIRA (
                CD_CONVENIO, CD_PACIENTE, CD_CON_PLA, NR_CARTEIRA,
                NM_TITULAR, SN_TITULAR, SN_CARTEIRA_ATIVO
            ) VALUES (
                :cd_convenio, :paciente, :cd_con_pla, :nr_carteira,
                :nm_titular, 'S', 'S'
            )
            """,
            {
                'cd_convenio': payload.cd_convenio,
                'paciente': paciente_id,
                'cd_con_pla': payload.cd_con_pla,
                'nr_carteira': nr_carteira,
                'nm_titular': nome_paciente,
            },
        )
        connection.commit()
    except HTTPException:
        connection.rollback()
        raise
    except Exception as exc:
        connection.rollback()
        logger.exception('Falha ao cadastrar paciente no MV')
        detalhe = _erro_oracle_resumido(exc)
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail=f'Nao foi possivel cadastrar o paciente no MV. Detalhe: {detalhe}',
        ) from exc
    finally:
        cursor.close()
    return {
        'cd_paciente': int(paciente_id),
        'nm_paciente': nome_paciente,
        'nr_cpf': cpf,
        'mensagem': 'Paciente cadastrado com sucesso no MV.',
    }


@router.put(
    '/pacientes/{cd_paciente}',
    status_code=HTTPStatus.OK,
    response_model=PacienteAtualizado,
)
def atualizar_paciente(
    cd_paciente: int,
    payload: AtualizacaoPacienteInput,
    usuario_atual: ValidaUsuarioAtual,
    session: Session = Depends(get_session_oracle),
):
    """Atualiza dados cadastrais simples do paciente antes do agendamento."""
    del usuario_atual
    if os.getenv('MV_GRAVACAO_HABILITADA', 'false').lower() != 'true':
        raise HTTPException(
            status_code=HTTPStatus.NOT_IMPLEMENTED,
            detail='Atualizacao de paciente desabilitada neste ambiente.',
        )
    if cd_paciente <= 0:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='Codigo do paciente invalido.',
        )
    if (payload.cd_convenio is None) != (payload.cd_con_pla is None):
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='Informe convenio e plano juntos.',
        )

    connection = session.connection().connection
    cursor = connection.cursor()
    try:
        cursor.callproc('DBAMV.PKG_MV2000.ATRIBUI_EMPRESA', [1])
        paciente = cursor.execute(
            """
            SELECT NM_PACIENTE
              FROM DBAMV.PACIENTE
             WHERE CD_PACIENTE = :cd_paciente
            """,
            {'cd_paciente': cd_paciente},
        ).fetchone()
        if not paciente:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail='Paciente nao encontrado no MV.',
            )

        cursor.execute(
            """
            UPDATE DBAMV.PACIENTE
               SET EMAIL = :email,
                   NR_DDD_CELULAR = :nr_ddd_celular,
                   NR_CELULAR = :nr_celular,
                   SN_ALT_DADOS_ORA_APP = 'S'
             WHERE CD_PACIENTE = :cd_paciente
            """,
            {
                'email': payload.email,
                'nr_ddd_celular': payload.nr_ddd_celular,
                'nr_celular': payload.nr_celular,
                'cd_paciente': cd_paciente,
            },
        )

        if payload.nr_celular:
            contato = cursor.execute(
                """
                SELECT CD_CONTATO_PACIENTE
                  FROM DBAMV.CONTATO_PACIENTE
                 WHERE CD_PACIENTE = :cd_paciente
                   AND TP_CONTATO = 'C'
                 ORDER BY NVL(SN_PADRAO, 'N') DESC, CD_CONTATO_PACIENTE DESC
                 FETCH FIRST 1 ROW ONLY
                """,
                {'cd_paciente': cd_paciente},
            ).fetchone()
            if contato:
                cursor.execute(
                    """
                    UPDATE DBAMV.CONTATO_PACIENTE
                       SET NR_DDD = :ddd,
                           NR_TELEFONE = :telefone,
                           DS_TIP_COMUN = 'CELULAR',
                           SN_PADRAO = 'S'
                     WHERE CD_CONTATO_PACIENTE = :cd_contato
                    """,
                    {
                        'ddd': payload.nr_ddd_celular,
                        'telefone': payload.nr_celular,
                        'cd_contato': contato[0],
                    },
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO DBAMV.CONTATO_PACIENTE (
                        CD_CONTATO_PACIENTE, CD_PACIENTE, NR_DDD, NR_TELEFONE,
                        DS_TIP_COMUN, TP_CONTATO, SN_PADRAO, SN_SMS
                    ) VALUES (
                        SEQ_CONTATO_PACIENTE.NEXTVAL, :cd_paciente, :ddd,
                        :telefone, 'CELULAR', 'C', 'S', 'N'
                    )
                    """,
                    {
                        'cd_paciente': cd_paciente,
                        'ddd': payload.nr_ddd_celular,
                        'telefone': payload.nr_celular,
                    },
                )

        if payload.email:
            contato_email = cursor.execute(
                """
                SELECT CD_OUTROS_CONTATOS_PACIENTE
                  FROM DBAMV.OUTROS_CONTATOS_PACIENTE
                 WHERE CD_PACIENTE = :cd_paciente
                   AND TP_OUTROS_CONTATO = 'E'
                 ORDER BY NVL(SN_PADRAO, 'N') DESC, CD_OUTROS_CONTATOS_PACIENTE DESC
                 FETCH FIRST 1 ROW ONLY
                """,
                {'cd_paciente': cd_paciente},
            ).fetchone()
            if contato_email:
                cursor.execute(
                    """
                    UPDATE DBAMV.OUTROS_CONTATOS_PACIENTE
                       SET CONTATO = :email,
                           SN_PADRAO = 'S',
                           DS_TIP_COMUN = 'E-MAIL'
                     WHERE CD_OUTROS_CONTATOS_PACIENTE = :cd_contato
                    """,
                    {'email': payload.email, 'cd_contato': contato_email[0]},
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO DBAMV.OUTROS_CONTATOS_PACIENTE (
                        CD_OUTROS_CONTATOS_PACIENTE, CD_PACIENTE,
                        TP_OUTROS_CONTATO, CONTATO, SN_PADRAO,
                        SN_RECEBE_CONTATO, DS_TIP_COMUN
                    ) VALUES (
                        SEQ_OUTROS_CONTATOS_PACIENTE.NEXTVAL, :cd_paciente,
                        'E', :email, 'S', 'N', 'E-MAIL'
                    )
                    """,
                    {'cd_paciente': cd_paciente, 'email': payload.email},
                )

        if payload.cd_convenio is not None and payload.cd_con_pla is not None:
            plano = cursor.execute(
                """
                SELECT 1
                  FROM DBAMV.CONVENIO c
                  JOIN DBAMV.CON_PLA cp ON cp.CD_CONVENIO = c.CD_CONVENIO
                 WHERE c.CD_CONVENIO = :cd_convenio
                   AND cp.CD_CON_PLA = :cd_con_pla
                   AND NVL(c.SN_ATIVO, 'S') = 'S'
                   AND NVL(cp.SN_ATIVO, 'S') = 'S'
                """,
                {
                    'cd_convenio': payload.cd_convenio,
                    'cd_con_pla': payload.cd_con_pla,
                },
            ).fetchone()
            if not plano:
                raise HTTPException(
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    detail='Convenio/plano invalido ou inativo no MV.',
                )
            carteira = cursor.execute(
                """
                SELECT 1
                  FROM DBAMV.CARTEIRA
                 WHERE CD_PACIENTE = :cd_paciente
                   AND CD_CONVENIO = :cd_convenio
                   AND CD_CON_PLA = :cd_con_pla
                 FETCH FIRST 1 ROW ONLY
                """,
                {
                    'cd_paciente': cd_paciente,
                    'cd_convenio': payload.cd_convenio,
                    'cd_con_pla': payload.cd_con_pla,
                },
            ).fetchone()
            if carteira:
                cursor.execute(
                    """
                    UPDATE DBAMV.CARTEIRA
                       SET NR_CARTEIRA = NVL(:nr_carteira, NR_CARTEIRA),
                           SN_CARTEIRA_ATIVO = 'S'
                     WHERE CD_PACIENTE = :cd_paciente
                       AND CD_CONVENIO = :cd_convenio
                       AND CD_CON_PLA = :cd_con_pla
                    """,
                    {
                        'nr_carteira': payload.nr_carteira,
                        'cd_paciente': cd_paciente,
                        'cd_convenio': payload.cd_convenio,
                        'cd_con_pla': payload.cd_con_pla,
                    },
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO DBAMV.CARTEIRA (
                        CD_CONVENIO, CD_PACIENTE, CD_CON_PLA, NR_CARTEIRA,
                        NM_TITULAR, SN_TITULAR, SN_CARTEIRA_ATIVO
                    ) VALUES (
                        :cd_convenio, :cd_paciente, :cd_con_pla,
                        :nr_carteira, :nm_titular, 'S', 'S'
                    )
                    """,
                    {
                        'cd_convenio': payload.cd_convenio,
                        'cd_paciente': cd_paciente,
                        'cd_con_pla': payload.cd_con_pla,
                        'nr_carteira': payload.nr_carteira,
                        'nm_titular': str(paciente[0]).strip().upper(),
                    },
                )
        connection.commit()
    except HTTPException:
        connection.rollback()
        raise
    except Exception as exc:
        connection.rollback()
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='Nao foi possivel atualizar os dados do paciente no MV.',
        ) from exc
    finally:
        cursor.close()

    return {
        'cd_paciente': cd_paciente,
        'mensagem': 'Dados do paciente atualizados com sucesso no MV.',
    }


@router.get(
    '/pacientes/{cd_paciente}/agendamentos',
    status_code=HTTPStatus.OK,
    response_model=AgendamentosPaciente,
)
def consultar_agendamentos_paciente(
    usuario_atual: ValidaUsuarioAtual,
    cd_paciente: int,
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    try:
        rows = (
            session
            .execute(
                CONSULTA_AGENDAMENTOS_PACIENTE,
                {'cd_paciente': cd_paciente},
            )
            .mappings()
            .all()
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel consultar os agendamentos no MV.',
        ) from exc
    unicos = {}
    for row in rows:
        registro = dict(row)
        chave = registro['cd_it_agenda_central']
        anterior = unicos.get(chave)
        if anterior is None or (
            registro.get('item_movimento_id') or 0
        ) > (anterior.get('item_movimento_id') or 0):
            unicos[chave] = registro
    agendamentos = list(unicos.values())
    return {'agendamentos': agendamentos, 'total': len(agendamentos)}


@router.get(
    '/pacientes/{cd_paciente}/historico',
    status_code=HTTPStatus.OK,
    response_model=HistoricoPaciente,
)
def consultar_historico_paciente(
    usuario_atual: ValidaUsuarioAtual,
    cd_paciente: int,
    session: Session = Depends(get_session_oracle),
):
    """Retorna o ultimo atendimento registrado no MV para o paciente."""
    del usuario_atual
    if cd_paciente <= 0:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='Codigo do paciente invalido.',
        )
    try:
        row = (
            session
            .execute(
                CONSULTA_ULTIMO_ATENDIMENTO_PACIENTE,
                {'cd_paciente': cd_paciente},
            )
            .mappings()
            .first()
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel consultar o historico do paciente no MV.',
        ) from exc

    if not row:
        return {'ultimo_atendimento': None, 'total': 0}
    return {'ultimo_atendimento': dict(row), 'total': 1}


@router.get(
    '/pacientes/{cd_paciente}/linhas-cuidado',
    status_code=HTTPStatus.OK,
    response_model=LinhasCuidadoPaciente,
)
def consultar_linhas_cuidado_paciente(
    usuario_atual: ValidaUsuarioAtual,
    cd_paciente: int,
    session: Session = Depends(get_session_oracle),
):
    """Retorna linhas de cuidado registradas para o paciente no MV."""
    del usuario_atual
    if cd_paciente <= 0:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='Codigo do paciente invalido.',
        )

    try:
        rows = (
            session
            .execute(
                CONSULTA_LINHAS_CUIDADO_PACIENTE,
                {'cd_paciente': cd_paciente},
            )
            .mappings()
            .all()
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel consultar as linhas de cuidado do paciente no MV.',
        ) from exc

    documentos = {}
    ordem = []
    for row in rows:
        cd_registro = row.get('cd_registro')
        chave = cd_registro or f"{row.get('cd_atendimento')}:{row.get('dh_documento')}"
        if chave not in documentos:
            documentos[chave] = {
                'cd_documento': row.get('cd_documento'),
                'cd_registro': cd_registro,
                'cd_atendimento': row.get('cd_atendimento'),
                'ds_documento': row.get('ds_documento'),
                'ds_tipo_documento': row.get('ds_tipo_documento'),
                'tp_status': row.get('tp_status'),
                'dh_documento': row.get('dh_documento'),
                'dh_fechamento': row.get('dh_fechamento'),
                'cd_usuario_criou': row.get('cd_usuario_criou'),
                'respostas': [],
            }
            ordem.append(chave)

        resposta = row.get('ds_resposta')
        campo = row.get('ds_campo_filho')
        identificador = row.get('ds_identificador_filho')
        if resposta is None or str(resposta).strip().lower() == 'null':
            continue
        if identificador and str(identificador).upper().startswith('PAR_'):
            continue
        documentos[chave]['respostas'].append(
            {
                'campo': campo,
                'identificador': identificador,
                'resposta': str(resposta),
            }
        )

    linhas = [documentos[chave] for chave in ordem[:5]]
    return {'linhas': linhas, 'total': len(linhas)}


@router.post(
    '/{cd_it_agenda_central}/cancelar',
    status_code=HTTPStatus.OK,
    response_model=AgendamentoCancelado,
)
def cancelar_agendamento(
    cd_it_agenda_central: int,
    payload: CancelarAgendamentoInput,
    usuario_atual: ValidaUsuarioAtual,
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    if os.getenv('MV_GRAVACAO_HABILITADA', 'false').lower() != 'true':
        raise HTTPException(
            status_code=HTTPStatus.NOT_IMPLEMENTED,
            detail='Gravacao do MV desabilitada neste ambiente.',
        )
    try:
        dados_whatsapp_cancelamento = (
            session.execute(
                CONSULTA_DADOS_CANCELAMENTO_WHATSAPP,
                {'cd_it_agenda_central': cd_it_agenda_central},
            )
            .mappings()
            .first()
        )
        dados_whatsapp_cancelamento = (
            dict(dados_whatsapp_cancelamento)
            if dados_whatsapp_cancelamento
            else None
        )
    except SQLAlchemyError:
        dados_whatsapp_cancelamento = None

    connection = session.connection().connection
    cursor = connection.cursor()
    retorno = cursor.var(str, 10)
    try:
        cursor.callproc(
            'DBAMV.PKG_AGENDAMENTO_WEB.PRC_EXCLUIR_AGD_WEB',
            [cd_it_agenda_central, payload.motivo, retorno],
        )
        codigo = retorno.getvalue()
        if codigo not in ('S', '1', 'OK', None):
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail=f'O MV recusou o cancelamento (retorno {codigo}).',
            )
        connection.commit()
    except HTTPException:
        raise
    except Exception as exc:
        connection.rollback()
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail='Falha ao cancelar o agendamento no MV.',
        ) from exc
    finally:
        cursor.close()

    whatsapp_status, whatsapp_mensagem = _enviar_cancelamento_whatsapp_agendamento(
        dados_whatsapp_cancelamento,
    )

    return {
        'status': 'cancelado',
        'mensagem': 'Agendamento cancelado com sucesso no MV.',
        'horario_id': cd_it_agenda_central,
        'retorno_mv': codigo,
        'whatsapp_status': whatsapp_status,
        'whatsapp_mensagem': whatsapp_mensagem,
    }


@router.get(
    '/itens',
    status_code=HTTPStatus.OK,
    response_model=ItensAgendamentoEncontrados,
)
def consultar_itens(
    usuario_atual: ValidaUsuarioAtual,
    termo: Annotated[str, Query(min_length=2, max_length=80)],
    limite: Annotated[int, Query(ge=1, le=50)] = 20,
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    termo_limpo = termo.strip()
    codigo = int(termo_limpo) if termo_limpo.isdigit() else -1

    try:
        rows = (
            session
            .execute(
                CONSULTA_ITENS,
                {
                    'codigo': codigo,
                    'descricao': f'%{termo_limpo.upper()}%',
                    'limite': limite,
                },
            )
            .mappings()
            .all()
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel consultar os itens no MV.',
        ) from exc

    itens = []
    for row in rows:
        item = dict(row)
        realizacao = item.pop('hr_realizacao', None)
        item['duracao_minutos'] = (
            realizacao.hour * 60 + realizacao.minute
            if isinstance(realizacao, datetime)
            else None
        )
        itens.append(item)
    return {'itens': itens, 'total': len(itens)}


@router.get(
    '/itens/{cd_item_agendamento}/orientacoes',
    status_code=HTTPStatus.OK,
    response_model=OrientacaoExame,
)
def consultar_orientacoes_item(
    usuario_atual: ValidaUsuarioAtual,
    cd_item_agendamento: int,
    session: Session = Depends(get_session_oracle),
):
    """Retorna as orientacoes/preparo de exame vinculadas ao item de agenda."""
    del usuario_atual
    if cd_item_agendamento <= 0:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='Codigo do item invalido.',
        )

    try:
        item = session.execute(
            text(
                """
                SELECT ia.CD_EXA_RX AS cd_exa_rx,
                       er.DS_EXA_RX AS ds_exa_rx
                  FROM DBAMV.ITEM_AGENDAMENTO ia
                  LEFT JOIN DBAMV.EXA_RX er
                    ON er.CD_EXA_RX = ia.CD_EXA_RX
                 WHERE ia.CD_ITEM_AGENDAMENTO = :cd_item_agendamento
                """
            ),
            {'cd_item_agendamento': cd_item_agendamento},
        ).mappings().first()
        if item is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail='Item de agendamento nao encontrado no MV.',
            )
        if item['cd_exa_rx'] is None:
            return {
                'cd_item_agendamento': cd_item_agendamento,
                'cd_exa_rx': None,
                'ds_exa_rx': None,
                'orientacoes': [],
                'total': 0,
            }

        rows = (
            session.execute(
                text(
                    """
                    SELECT DS_ORIENTACAO AS ds_orientacao
                      FROM DBAMV.EMPRESA_ORIENTACOES_EXA_RX
                     WHERE CD_EXA_RX = :cd_exa_rx
                       AND (CD_MULTI_EMPRESA = 1 OR CD_MULTI_EMPRESA IS NULL)
                       AND DS_ORIENTACAO IS NOT NULL
                     ORDER BY NVL(CD_MULTI_EMPRESA, 0)
                    """
                ),
                {'cd_exa_rx': item['cd_exa_rx']},
            )
            .mappings()
            .all()
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel consultar as orientacoes do exame no MV.',
        ) from exc

    orientacoes = [
        row['ds_orientacao'].strip()
        for row in rows
        if row['ds_orientacao'] and row['ds_orientacao'].strip()
    ]
    return {
        'cd_item_agendamento': cd_item_agendamento,
        'cd_exa_rx': item['cd_exa_rx'],
        'ds_exa_rx': item['ds_exa_rx'],
        'orientacoes': orientacoes,
        'total': len(orientacoes),
    }


@router.get('/regras')
def consultar_regras_agendamento(
    usuario_atual: ValidaUsuarioAtual,
    cd_item_agendamento: Annotated[int, Query(gt=0)],
    cd_convenio: Annotated[int, Query(gt=0)],
    cd_con_pla: Annotated[int, Query(gt=0)],
    session: Session = Depends(get_session_oracle),
):
    """Retorna sinalizacoes de autorizacao e filtros usados pela Central do MV."""
    del usuario_atual
    consulta = text('''
        SELECT c.NM_CONVENIO AS nm_convenio,
               c.SN_GUIA AS sn_guia,
               c.TP_AUTORIZ_CENTRAL_AGENDAMENTO AS tp_autoriz_central,
               c.SN_OBRIGA_PLANO_AGENDA AS sn_obriga_plano,
               cp.DS_CON_PLA AS ds_con_pla,
               cs.SN_VERIFICA_PROIBICAO_AGD AS sn_verifica_proibicao,
               cs.SN_VALIDA_CONVENIO_ITEM AS sn_valida_convenio_item,
               cs.SN_AGENDAMENTO_WEB AS sn_agendamento_web
          FROM DBAMV.CONVENIO c
          JOIN DBAMV.CON_PLA cp ON cp.CD_CONVENIO = c.CD_CONVENIO
          CROSS JOIN DBAMV.CONFIG_SCMA cs
         WHERE c.CD_CONVENIO = :cd_convenio
           AND cp.CD_CON_PLA = :cd_con_pla
           AND cs.CD_MULTI_EMPRESA = 1
    ''')
    item = session.execute(
        text('SELECT DS_ITEM_AGENDAMENTO FROM DBAMV.ITEM_AGENDAMENTO WHERE CD_ITEM_AGENDAMENTO = :item'),
        {'item': cd_item_agendamento},
    ).scalar()
    row = session.execute(
        consulta,
        {'cd_convenio': cd_convenio, 'cd_con_pla': cd_con_pla},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail='Convenio/plano nao encontrado no MV.')
    dados = dict(row)
    alertas = [
        'O MV pode aplicar proibicoes por item, convenio, empresa e data.',
        'A disponibilidade exibida depende da unidade, setor, recurso e modalidade da agenda.',
    ]
    if dados['sn_guia'] == 'S' or dados['tp_autoriz_central'] not in (None, 'N'):
        alertas.insert(0, 'Este convenio exige autorizacao/guia para o agendamento.')
    if dados['sn_verifica_proibicao'] == 'S':
        alertas.insert(0, 'A Central do MV esta configurada para verificar proibicoes de agendamento.')
    return {
        'item': {'cd_item_agendamento': cd_item_agendamento, 'ds_item_agendamento': item},
        'convenio': dados,
        'alertas': alertas,
    }


@router.get(
    '/pacientes/{cd_paciente}/convenios',
    status_code=HTTPStatus.OK,
    response_model=ConveniosPaciente,
)
def consultar_convenios_paciente(
    usuario_atual: ValidaUsuarioAtual,
    cd_paciente: int,
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    if cd_paciente <= 0:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='Codigo do paciente invalido.',
        )

    try:
        rows = (
            session
            .execute(
                CONSULTA_CONVENIOS_PACIENTE,
                {'cd_paciente': cd_paciente},
            )
            .mappings()
            .all()
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel consultar os convenios no MV.',
        ) from exc

    convenios = [dict(row) for row in rows]
    return {'convenios': convenios, 'total': len(convenios)}


@router.get(
    '/itens/{cd_item_agendamento}/prestadores',
    status_code=HTTPStatus.OK,
    response_model=PrestadoresAgendamento,
)
def consultar_prestadores_item(  # noqa: PLR0913
    usuario_atual: ValidaUsuarioAtual,
    cd_item_agendamento: int,
    limite: Annotated[int, Query(ge=1, le=100)] = 50,
    data_inicio: date | None = None,
    data_fim: date | None = None,
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    if cd_item_agendamento <= 0:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='Codigo do item invalido.',
        )

    inicio = data_inicio or date.today()
    fim = data_fim or inicio + timedelta(days=30)
    if fim < inicio or fim - inicio > timedelta(days=90):
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='Periodo de consulta de prestadores invalido.',
        )

    try:
        rows = (
            session
            .execute(
                CONSULTA_PRESTADORES_ITEM,
                {
                    'cd_item_agendamento': cd_item_agendamento,
                    'limite': limite,
                    'data_inicio': inicio,
                    'data_fim': fim,
                },
            )
            .mappings()
            .all()
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel consultar os prestadores no MV.',
        ) from exc

    prestadores = [dict(row) for row in rows]
    descricao_item = session.scalar(
        text('SELECT DS_ITEM_AGENDAMENTO FROM DBAMV.ITEM_AGENDAMENTO WHERE CD_ITEM_AGENDAMENTO = :item'),
        {'item': cd_item_agendamento},
    ) or ''
    eh_consulta = 'CONSULTA' in descricao_item.upper()
    if not eh_consulta:
        prestadores = []
    exige_prestador = bool(prestadores) and eh_consulta
    if not exige_prestador:
        exige_prestador = eh_consulta and bool(
            session.scalar(
                CONSULTA_ITEM_EXIGE_PRESTADOR,
                {'cd_item_agendamento': cd_item_agendamento},
            )
        )
    return {
        'prestadores': prestadores,
        'total': len(prestadores),
        'exige_prestador': exige_prestador,
    }


@router.post(
    '/pre-validar',
    status_code=HTTPStatus.OK,
    response_model=PreValidacaoAgendamento,
)
def pre_validar_agendamento(
    payload: PreValidacaoAgendamentoInput,
    usuario_atual: ValidaUsuarioAtual,
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    parametros = payload.model_dump()

    try:
        row = (
            session
            .execute(CONSULTA_PRE_VALIDACAO, parametros)
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail=(
                    'O horario nao corresponde aos dados selecionados. '
                    'Atualize a disponibilidade.'
                ),
            )

        dados = dict(row)
        if (
            dados['slot_cd_paciente'] is not None
            or dados['slot_dt_gravacao'] is not None
            or dados['slot_bloqueado'] == 'S'
            or dados['horario'] <= datetime.now()
        ):
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail='O horario nao esta mais disponivel.',
            )

        prestador_agenda = dados['cd_prestador']
        if prestador_agenda is not None and payload.cd_prestador is None:
            raise HTTPException(
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                detail='Selecione o medico/prestador da agenda.',
            )
        if payload.cd_prestador != prestador_agenda:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail='O horario pertence a outro medico/prestador.',
            )

        duplicado = (
            session
            .execute(
                CONSULTA_AGENDAMENTO_DUPLICADO,
                {
                    'cd_paciente': payload.cd_paciente,
                    'cd_item_agendamento': payload.cd_item_agendamento,
                },
            )
            .mappings()
            .first()
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel pre-validar o agendamento no MV.',
        ) from exc

    alertas = []
    if duplicado:
        alertas.append(
            'O paciente ja possui agendamento futuro para este item.'
        )

    for campo in (
        'slot_cd_paciente',
        'slot_dt_gravacao',
        'slot_bloqueado',
    ):
        dados.pop(campo)
    dados['pode_agendar'] = not duplicado
    dados['alertas'] = alertas
    dados['agendamento_existente_slot'] = (
        duplicado['cd_it_agenda_central'] if duplicado else None
    )
    dados['agendamento_existente_horario'] = (
        duplicado['horario'] if duplicado else None
    )
    return dados


@router.get(
    '/horarios',
    status_code=HTTPStatus.OK,
    response_model=HorariosDisponiveis,
)
def consultar_horarios(  # noqa: PLR0913
    usuario_atual: ValidaUsuarioAtual,
    cd_item_agendamento: Annotated[int, Query(gt=0)],
    data_inicio: date | None = None,
    data_fim: date | None = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    cd_prestador: Annotated[int | None, Query(gt=0)] = None,
    cd_tip_mar: Annotated[int | None, Query(gt=0)] = None,
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    inicio = data_inicio or date.today()
    fim = data_fim or inicio + timedelta(days=30)

    if fim < inicio:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='data_fim deve ser igual ou posterior a data_inicio.',
        )
    if fim - inicio > timedelta(days=90):
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='O periodo maximo de consulta e de 90 dias.',
        )

    try:
        rows = (
            session
            .execute(
                CONSULTA_HORARIOS,
                {
                    'cd_item_agendamento': cd_item_agendamento,
                    'data_inicio': inicio,
                    'data_fim': fim,
                    'limite': limite,
                    'cd_prestador': cd_prestador,
                    'cd_tip_mar': cd_tip_mar,
                },
            )
            .mappings()
            .all()
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Nao foi possivel consultar os horarios no MV.',
        ) from exc

    horarios = [dict(row) for row in rows]
    for horario in horarios:
        unidade = (horario.get('ds_unidade_atendimento') or '').strip().upper()
        if unidade == 'CLINICA PRONTOCARDIO SAUDE':
            horario['ds_unidade_atendimento'] = 'CLÍNICA DIAGNÓSTICA 01'
        elif unidade == 'UNIDADE DIAGNOSTICO 2':
            horario['ds_unidade_atendimento'] = 'CLÍNICA DIAGNÓSTICA 02'
    return {'horarios': horarios, 'total': len(horarios)}
