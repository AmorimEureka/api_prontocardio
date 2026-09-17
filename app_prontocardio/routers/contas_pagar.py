# ruff: noqa: E501, PLR0913

from datetime import date
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from math import ceil
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app_prontocardio.database import get_session_oracle, get_session_postgres
from app_prontocardio.models import Usuario
from app_prontocardio.security import valida_token_usuario_atual

router = APIRouter(
    prefix='/app_glosas/financeiro/contas-a-pagar', tags=['contas-a-pagar']
)
SessionPostgres = Annotated[Session, Depends(get_session_postgres)]
SessionOracle = Annotated[Session, Depends(get_session_oracle)]
UsuarioAtual = Annotated[Usuario, Depends(valida_token_usuario_atual)]
CENTAVOS = Decimal('0.01')
STATUS_VALIDOS = {'PENDENTE', 'CONTATO', 'NEGOCIACAO', 'ACORDADO'}


class TratamentoInput(BaseModel):
    critico: bool = False
    pagamento_imediato: Decimal = Field(default=Decimal('0'), ge=0)
    status: Literal['PENDENTE', 'CONTATO', 'NEGOCIACAO', 'ACORDADO'] = (
        'PENDENTE'
    )
    responsavel: str | None = Field(default=None, max_length=150)
    proxima_acao: str | None = Field(default=None, max_length=255)
    data_proxima_acao: date | None = None
    condicao_negociada: str | None = Field(default=None, max_length=4000)
    observacao: str | None = Field(default=None, max_length=4000)

    @field_validator(
        'responsavel', 'proxima_acao', 'condicao_negociada', 'observacao'
    )
    @classmethod
    def normalizar_texto(cls, value):
        return str(value).strip() or None if value is not None else None


ORACLE_CONTAS_QUERY = text("""
WITH pagamentos_distintos AS (
    SELECT codigo_parcela_pk,
           codigo_pagamento_pk,
           MAX(NVL(valor_pago, 0)) AS valor_pago
      FROM dbamv.HPC_V_CONTAS_A_PAGAR
     WHERE codigo_pagamento_pk IS NOT NULL
       AND data_de_estorno IS NULL
     GROUP BY codigo_parcela_pk, codigo_pagamento_pk
),
pagamentos AS (
    SELECT codigo_parcela_pk, SUM(valor_pago) AS valor_pago
      FROM pagamentos_distintos
     GROUP BY codigo_parcela_pk
),
parcelas AS (
    SELECT v.codigo_do_fornecedor AS codigo_fornecedor,
           MAX(v.nome_fornecedor) AS nome_fornecedor,
           v.codigo_parcela_pk,
           MAX(NVL(v.valor_da_duplicata, 0)) AS valor_duplicata,
           MIN(TO_DATE(v.dt_vencimento, 'DD/MM/YYYY')) AS data_vencimento,
           MAX(v.tipo_de_quitacao) AS tipo_quitacao
      FROM dbamv.HPC_V_CONTAS_A_PAGAR v
     GROUP BY v.codigo_do_fornecedor, v.codigo_parcela_pk
),
saldos AS (
    SELECT p.codigo_fornecedor,
           p.nome_fornecedor,
           p.data_vencimento,
           GREATEST(p.valor_duplicata - NVL(pg.valor_pago, 0), 0) AS saldo
      FROM parcelas p
      LEFT JOIN pagamentos pg ON pg.codigo_parcela_pk = p.codigo_parcela_pk
     WHERE p.tipo_quitacao IN ('previsto', 'comprometido', 'parcialmente pago')
)
SELECT codigo_fornecedor,
       MAX(nome_fornecedor) AS nome_fornecedor,
       SUM(CASE WHEN data_vencimento < TRUNC(SYSDATE) THEN saldo ELSE 0 END) AS valor_vencido,
       SUM(CASE WHEN data_vencimento >= TRUNC(SYSDATE) THEN saldo ELSE 0 END) AS valor_corrente,
       MIN(CASE WHEN data_vencimento < TRUNC(SYSDATE) THEN data_vencimento END) AS vencimento_mais_antigo,
       SUM(CASE WHEN data_vencimento BETWEEN TRUNC(SYSDATE) - 6 AND TRUNC(SYSDATE) - 1 THEN saldo ELSE 0 END) AS novos_vencidos_7d,
       COUNT(CASE WHEN data_vencimento < TRUNC(SYSDATE) AND saldo > 0 THEN 1 END) AS titulos_vencidos,
       COUNT(CASE WHEN data_vencimento >= TRUNC(SYSDATE) AND saldo > 0 THEN 1 END) AS titulos_correntes
  FROM saldos
 WHERE saldo > 0
 GROUP BY codigo_fornecedor
""")


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(CENTAVOS)
    except (InvalidOperation, ValueError):
        return Decimal('0.00')


def _serializar_decimal(value: Decimal) -> str:
    return f'{value.quantize(CENTAVOS):.2f}'


def _consultar_oracle(session: Session) -> list[dict]:
    try:
        rows = session.execute(ORACLE_CONTAS_QUERY).mappings().all()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Não foi possível consultar a HPC_V_CONTAS_A_PAGAR.',
        ) from exc
    hoje = date.today()
    resultado = []
    for row in rows:
        vencimento = row['vencimento_mais_antigo']
        resultado.append({
            'codigo_fornecedor': int(row['codigo_fornecedor']),
            'nome_fornecedor': row['nome_fornecedor'] or 'Fornecedor sem nome',
            'valor_vencido': _decimal(row['valor_vencido']),
            'valor_corrente': _decimal(row['valor_corrente']),
            'vencimento_mais_antigo': vencimento,
            'dias_atraso': max((hoje - vencimento).days, 0)
            if vencimento
            else 0,
            'novos_vencidos_7d': _decimal(row['novos_vencidos_7d']),
            'titulos_vencidos': int(row['titulos_vencidos'] or 0),
            'titulos_correntes': int(row['titulos_correntes'] or 0),
        })
    return resultado


def _tratamentos(session: Session) -> dict[int, dict]:
    rows = session.execute(
        text("""
        SELECT t.*, u.nome AS usuario_nome
          FROM api_prontocardio.contas_pagar_tratamentos t
          JOIN api_prontocardio.usuarios_api u ON u.id = t.usuario_id
    """)
    ).mappings()
    return {int(row['codigo_fornecedor']): dict(row) for row in rows}


def _registrar_snapshot(session: Session, fornecedores: list[dict]) -> None:
    hoje = date.today()
    total_vencido = sum(
        (item['valor_vencido'] for item in fornecedores), Decimal('0')
    )
    novos = sum(
        (item['novos_vencidos_7d'] for item in fornecedores), Decimal('0')
    )
    corrente = sum(
        (item['valor_corrente'] for item in fornecedores), Decimal('0')
    )
    quantidade = sum(1 for item in fornecedores if item['valor_vencido'] > 0)
    session.execute(
        text("""
        INSERT INTO api_prontocardio.contas_pagar_snapshots
            (data_referencia, valor_vencido, novos_vencidos, valor_corrente, fornecedores_vencidos)
        VALUES (:data, :vencido, :novos, :corrente, :quantidade)
        ON CONFLICT (data_referencia) DO UPDATE SET
            valor_vencido = EXCLUDED.valor_vencido,
            novos_vencidos = EXCLUDED.novos_vencidos,
            valor_corrente = EXCLUDED.valor_corrente,
            fornecedores_vencidos = EXCLUDED.fornecedores_vencidos,
            data_registro = timezone('America/Sao_Paulo', now())
    """),
        {
            'data': hoje,
            'vencido': total_vencido,
            'novos': novos,
            'corrente': corrente,
            'quantidade': quantidade,
        },
    )
    session.commit()


def _historico(session: Session) -> list[dict]:
    rows = (
        session
        .execute(
            text("""
        SELECT data_referencia, valor_vencido, novos_vencidos, valor_corrente, fornecedores_vencidos
          FROM api_prontocardio.contas_pagar_snapshots
         ORDER BY data_referencia DESC
         LIMIT 13
    """)
        )
        .mappings()
        .all()
    )
    return [
        {
            'data_referencia': row['data_referencia'].isoformat(),
            'valor_vencido': _serializar_decimal(
                _decimal(row['valor_vencido'])
            ),
            'novos_vencidos': _serializar_decimal(
                _decimal(row['novos_vencidos'])
            ),
            'valor_corrente': _serializar_decimal(
                _decimal(row['valor_corrente'])
            ),
            'fornecedores_vencidos': int(row['fornecedores_vencidos']),
        }
        for row in reversed(rows)
    ]


@router.get('')
def listar_contas_pagar(
    _: UsuarioAtual,
    session: SessionPostgres,
    oracle: SessionOracle,
    q: str | None = None,
    criticidade: Literal['todos', 'criticos', 'nao_criticos'] = 'todos',
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    fornecedores = _consultar_oracle(oracle)
    tratamentos = _tratamentos(session)
    for item in fornecedores:
        tratamento = tratamentos.get(item['codigo_fornecedor'], {})
        item.update({
            'critico': bool(tratamento.get('critico', False)),
            'pagamento_imediato': _decimal(
                tratamento.get('pagamento_imediato')
            ),
            'status': tratamento.get('status') or 'PENDENTE',
            'responsavel': tratamento.get('responsavel'),
            'proxima_acao': tratamento.get('proxima_acao'),
            'data_proxima_acao': tratamento.get('data_proxima_acao'),
            'condicao_negociada': tratamento.get('condicao_negociada'),
            'observacao': tratamento.get('observacao'),
            'usuario_atualizacao': tratamento.get('usuario_nome'),
            'data_atualizacao': tratamento.get('data_atualizacao'),
        })
        item['saldo_negociar'] = max(
            item['valor_vencido'] - item['pagamento_imediato'], Decimal('0')
        )

    vencidos = [item for item in fornecedores if item['valor_vencido'] > 0]
    _registrar_snapshot(session, fornecedores)
    resumo = {
        'valor_vencido_atual': sum(
            (item['valor_vencido'] for item in vencidos), Decimal('0')
        ),
        'valor_corrente': sum(
            (item['valor_corrente'] for item in fornecedores), Decimal('0')
        ),
        'novos_vencidos_7d': sum(
            (item['novos_vencidos_7d'] for item in vencidos), Decimal('0')
        ),
        'pagamento_imediato': sum(
            (item['pagamento_imediato'] for item in vencidos), Decimal('0')
        ),
        'saldo_negociar': sum(
            (item['saldo_negociar'] for item in vencidos), Decimal('0')
        ),
        'fornecedores_vencidos': len(vencidos),
        'fornecedores_criticos': sum(
            1 for item in vencidos if item['critico']
        ),
    }
    historico = _historico(session)
    resumo['valor_vencido_inicial'] = (
        _decimal(historico[0]['valor_vencido'])
        if historico
        else resumo['valor_vencido_atual']
    )
    resumo['variacao_desde_inicio'] = (
        resumo['valor_vencido_atual'] - resumo['valor_vencido_inicial']
    )

    termo = (q or '').strip().casefold()
    filtrados = vencidos
    if termo:
        filtrados = [
            item
            for item in filtrados
            if termo in item['nome_fornecedor'].casefold()
            or termo in str(item['codigo_fornecedor'])
        ]
    if criticidade == 'criticos':
        filtrados = [item for item in filtrados if item['critico']]
    elif criticidade == 'nao_criticos':
        filtrados = [item for item in filtrados if not item['critico']]
    if status and status.upper() in STATUS_VALIDOS:
        filtrados = [
            item for item in filtrados if item['status'] == status.upper()
        ]
    filtrados.sort(
        key=lambda item: (
            not item['critico'],
            -item['dias_atraso'],
            -item['valor_vencido'],
        )
    )
    total = len(filtrados)
    inicio = (page - 1) * page_size

    def serializar(item):
        return {
            **item,
            'valor_vencido': _serializar_decimal(item['valor_vencido']),
            'valor_corrente': _serializar_decimal(item['valor_corrente']),
            'novos_vencidos_7d': _serializar_decimal(
                item['novos_vencidos_7d']
            ),
            'pagamento_imediato': _serializar_decimal(
                item['pagamento_imediato']
            ),
            'saldo_negociar': _serializar_decimal(item['saldo_negociar']),
        }

    return {
        'fornecedores': [
            serializar(item) for item in filtrados[inicio : inicio + page_size]
        ],
        'total': total,
        'page': page,
        'total_pages': max(ceil(total / page_size), 1),
        'resumo': {
            key: _serializar_decimal(value)
            if isinstance(value, Decimal)
            else value
            for key, value in resumo.items()
        },
        'historico': historico,
        'gerado_em': date.today().isoformat(),
    }


@router.put('/fornecedores/{codigo_fornecedor}')
def salvar_tratamento(
    codigo_fornecedor: int,
    payload: TratamentoInput,
    usuario: UsuarioAtual,
    session: SessionPostgres,
):
    session.execute(
        text("""
        INSERT INTO api_prontocardio.contas_pagar_tratamentos
            (codigo_fornecedor, critico, pagamento_imediato, status, responsavel,
             proxima_acao, data_proxima_acao, condicao_negociada, observacao, usuario_id)
        VALUES (:codigo, :critico, :pagamento, :status, :responsavel,
                :proxima_acao, :data_proxima_acao, :condicao, :observacao, :usuario)
        ON CONFLICT (codigo_fornecedor) DO UPDATE SET
            critico = EXCLUDED.critico,
            pagamento_imediato = EXCLUDED.pagamento_imediato,
            status = EXCLUDED.status,
            responsavel = EXCLUDED.responsavel,
            proxima_acao = EXCLUDED.proxima_acao,
            data_proxima_acao = EXCLUDED.data_proxima_acao,
            condicao_negociada = EXCLUDED.condicao_negociada,
            observacao = EXCLUDED.observacao,
            usuario_id = EXCLUDED.usuario_id,
            data_atualizacao = timezone('America/Sao_Paulo', now())
    """),
        {
            'codigo': codigo_fornecedor,
            'critico': payload.critico,
            'pagamento': payload.pagamento_imediato,
            'status': payload.status,
            'responsavel': payload.responsavel,
            'proxima_acao': payload.proxima_acao,
            'data_proxima_acao': payload.data_proxima_acao,
            'condicao': payload.condicao_negociada,
            'observacao': payload.observacao,
            'usuario': usuario.id,
        },
    )
    session.commit()
    return {'detail': 'Tratamento salvo com sucesso.'}


@router.delete(
    '/fornecedores/{codigo_fornecedor}', status_code=HTTPStatus.NO_CONTENT
)
def excluir_tratamento(
    codigo_fornecedor: int,
    _: UsuarioAtual,
    session: SessionPostgres,
):
    session.execute(
        text("""
        DELETE FROM api_prontocardio.contas_pagar_tratamentos
         WHERE codigo_fornecedor = :codigo
    """),
        {'codigo': codigo_fornecedor},
    )
    session.commit()
    return Response(status_code=HTTPStatus.NO_CONTENT)
