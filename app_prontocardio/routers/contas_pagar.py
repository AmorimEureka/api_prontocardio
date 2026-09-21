# ruff: noqa: E501, PLR0913

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from math import ceil
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import bindparam, text
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


class PagamentoTituloInput(BaseModel):
    data_pagamento: date
    valor_pago: Decimal = Field(gt=0, max_digits=15, decimal_places=2)
    banco: str = Field(min_length=1, max_length=150)
    agencia: str = Field(min_length=1, max_length=30)
    numero_conta: str = Field(min_length=1, max_length=50)
    observacao: str | None = Field(default=None, max_length=4000)

    @field_validator('banco', 'agencia', 'numero_conta')
    @classmethod
    def normalizar_origem_recurso(cls, value, info):
        value = str(value).strip()
        if not value:
            rotulos = {
                'banco': 'nome do banco',
                'agencia': 'agência',
                'numero_conta': 'número da conta',
            }
            raise ValueError(
                f'Informe {rotulos.get(info.field_name, info.field_name)} '
                'de origem do recurso.'
            )
        return value

    @field_validator('observacao')
    @classmethod
    def normalizar_observacao(cls, value):
        return str(value).strip() or None if value is not None else None


class CriticidadeFornecedorInput(BaseModel):
    critico: bool


ORACLE_FORNECEDORES_QUERY = text("""
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
     WHERE v.codigo_do_fornecedor IS NOT NULL
     GROUP BY v.codigo_do_fornecedor, v.codigo_parcela_pk
),
saldos AS (
    SELECT p.codigo_fornecedor,
           p.nome_fornecedor,
           p.data_vencimento,
           p.valor_duplicata,
           LEAST(p.valor_duplicata, NVL(pg.valor_pago, 0)) AS valor_honrado,
           GREATEST(p.valor_duplicata - NVL(pg.valor_pago, 0), 0) AS saldo
      FROM parcelas p
      LEFT JOIN pagamentos pg ON pg.codigo_parcela_pk = p.codigo_parcela_pk
     WHERE p.tipo_quitacao IN (
           'previsto', 'comprometido', 'parcialmente pago'
     )
       AND (:data_inicio IS NULL OR p.data_vencimento >= :data_inicio)
       AND (:data_fim IS NULL OR p.data_vencimento <= :data_fim)
)
SELECT codigo_fornecedor,
       MAX(nome_fornecedor) AS nome_fornecedor,
       SUM(valor_duplicata) AS valor_total,
       SUM(valor_honrado) AS valor_total_honrado,
       SUM(saldo) AS saldo_a_pagar,
       SUM(CASE WHEN data_vencimento < TRUNC(SYSDATE) THEN saldo ELSE 0 END) AS valor_total_vencido,
       SUM(CASE WHEN data_vencimento >= TRUNC(SYSDATE) THEN saldo ELSE 0 END) AS valor_corrente,
       MIN(CASE WHEN data_vencimento < TRUNC(SYSDATE) AND saldo > 0 THEN data_vencimento END) AS vencimento_mais_antigo,
       SUM(CASE WHEN data_vencimento BETWEEN TRUNC(SYSDATE) - 6 AND TRUNC(SYSDATE) - 1 THEN saldo ELSE 0 END) AS novos_vencidos_7d,
       COUNT(CASE WHEN data_vencimento < TRUNC(SYSDATE) AND saldo > 0 THEN 1 END) AS titulos_vencidos,
       COUNT(CASE WHEN data_vencimento >= TRUNC(SYSDATE) AND saldo > 0 THEN 1 END) AS titulos_correntes
  FROM saldos
 GROUP BY codigo_fornecedor
HAVING SUM(saldo) > 0
""")


ORACLE_TITULOS_QUERY = text("""
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
           MAX(v.codigo_do_contas_a_pagar) AS codigo_contas_pagar,
           MAX(v.numero_do_documento) AS numero_documento,
           MAX(v.descricao_da_conta) AS descricao_conta,
           MAX(v.numero_da_parcela) AS numero_parcela,
           MAX(v.data_de_lancamento) AS data_lancamento,
           MAX(v.data_de_emissao) AS data_emissao,
           MAX(NVL(v.valor_da_duplicata, 0)) AS valor_duplicata,
           MIN(TO_DATE(v.dt_vencimento, 'DD/MM/YYYY')) AS data_vencimento,
           MAX(v.tipo_de_quitacao) AS tipo_quitacao
     FROM dbamv.HPC_V_CONTAS_A_PAGAR v
     WHERE v.codigo_do_fornecedor IS NOT NULL
       AND v.codigo_do_fornecedor IN :codigos_fornecedor
     GROUP BY v.codigo_do_fornecedor, v.codigo_parcela_pk
)
SELECT p.codigo_fornecedor,
       p.nome_fornecedor,
       p.codigo_parcela_pk,
       p.codigo_contas_pagar,
       p.numero_documento,
       p.descricao_conta,
       p.numero_parcela,
       p.data_lancamento,
       p.data_emissao,
       p.valor_duplicata,
       p.data_vencimento,
       p.tipo_quitacao,
       NVL(pg.valor_pago, 0) AS valor_honrado_oracle
  FROM parcelas p
  LEFT JOIN pagamentos pg ON pg.codigo_parcela_pk = p.codigo_parcela_pk
 WHERE p.tipo_quitacao IN (
       'previsto', 'comprometido', 'parcialmente pago'
 )
   AND (:data_inicio IS NULL OR p.data_vencimento >= :data_inicio)
   AND (:data_fim IS NULL OR p.data_vencimento <= :data_fim)
 ORDER BY p.codigo_fornecedor, p.data_vencimento, p.codigo_parcela_pk
""").bindparams(bindparam('codigos_fornecedor', expanding=True))


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(CENTAVOS)
    except (InvalidOperation, ValueError):
        return Decimal('0.00')


def _serializar_decimal(value: Decimal) -> str:
    return f'{value.quantize(CENTAVOS):.2f}'


def _normalizar_data(value):
    return value.date() if isinstance(value, datetime) else value


def _consultar_oracle(
    session: Session,
    data_inicio: date | None = None,
    data_fim: date | None = None,
) -> list[dict]:
    try:
        rows = session.execute(
            ORACLE_FORNECEDORES_QUERY,
            {'data_inicio': data_inicio, 'data_fim': data_fim},
        ).mappings().all()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Não foi possível consultar a HPC_V_CONTAS_A_PAGAR.',
        ) from exc
    hoje = date.today()
    resultado = []
    for row in rows:
        if row['codigo_fornecedor'] is None:
            continue
        vencimento = _normalizar_data(row['vencimento_mais_antigo'])
        dias_atraso = (
            max((hoje - vencimento).days, 0) if vencimento else 0
        )
        resultado.append({
            'codigo_fornecedor': int(row['codigo_fornecedor']),
            'nome_fornecedor': row['nome_fornecedor'] or 'Fornecedor sem nome',
            'valor_total': _decimal(row['valor_total']),
            'valor_total_vencido': _decimal(row['valor_total_vencido']),
            'valor_total_honrado': _decimal(row['valor_total_honrado']),
            'saldo_a_pagar': _decimal(row['saldo_a_pagar']),
            'valor_corrente': _decimal(row['valor_corrente']),
            'vencimento_mais_antigo': vencimento,
            'dias_atraso': dias_atraso,
            'total_dias_vencidos': dias_atraso,
            'novos_vencidos_7d': _decimal(row['novos_vencidos_7d']),
            'titulos_vencidos': int(row['titulos_vencidos'] or 0),
            'titulos_correntes': int(row['titulos_correntes'] or 0),
            'titulos': [],
        })
    return resultado


def _consultar_titulos_oracle(
    session: Session,
    codigos_fornecedor: list[int],
    data_inicio: date | None = None,
    data_fim: date | None = None,
) -> list[dict]:
    if not codigos_fornecedor:
        return []
    try:
        rows = session.execute(
            ORACLE_TITULOS_QUERY,
            {
                'codigos_fornecedor': codigos_fornecedor,
                'data_inicio': data_inicio,
                'data_fim': data_fim,
            },
        ).mappings().all()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail='Não foi possível consultar a HPC_V_CONTAS_A_PAGAR.',
        ) from exc
    resultado = []
    for row in rows:
        if row['codigo_fornecedor'] is None or row['codigo_parcela_pk'] is None:
            continue
        resultado.append({
            'codigo_fornecedor': int(row['codigo_fornecedor']),
            'nome_fornecedor': row['nome_fornecedor'] or 'Fornecedor sem nome',
            'codigo_parcela': int(row['codigo_parcela_pk']),
            'codigo_contas_pagar': int(row['codigo_contas_pagar'])
            if row['codigo_contas_pagar'] is not None
            else None,
            'numero_documento': row['numero_documento'],
            'descricao_conta': row['descricao_conta'],
            'numero_parcela': row['numero_parcela'],
            'data_lancamento': _normalizar_data(row['data_lancamento']),
            'data_emissao': _normalizar_data(row['data_emissao']),
            'valor_total': _decimal(row['valor_duplicata']),
            'data_vencimento': _normalizar_data(row['data_vencimento']),
            'tipo_quitacao': row['tipo_quitacao'],
            'valor_honrado_oracle': _decimal(
                row['valor_honrado_oracle']
            ),
        })
    return resultado


def _pagamentos_manuais(session: Session) -> dict[int, list[dict]]:
    rows = session.execute(
        text("""
        SELECT p.*, u.nome AS usuario_nome
          FROM api_prontocardio.contas_pagar_pagamentos p
          JOIN api_prontocardio.usuarios_api u ON u.id = p.usuario_id
         ORDER BY p.data_pagamento DESC, p.id DESC
    """)
    ).mappings()
    pagamentos = {}
    for row in rows:
        item = dict(row)
        item['valor_pago'] = _decimal(item['valor_pago'])
        pagamentos.setdefault(int(item['codigo_parcela']), []).append(item)
    return pagamentos


def _agrupar_fornecedores(
    titulos: list[dict], pagamentos_por_titulo: dict[int, list[dict]]
) -> list[dict]:
    hoje = date.today()
    fornecedores = {}
    for titulo_oracle in titulos:
        titulo = dict(titulo_oracle)
        pagamentos = pagamentos_por_titulo.get(titulo['codigo_parcela'], [])
        valor_manual = sum(
            (item['valor_pago'] for item in pagamentos), Decimal('0')
        )
        valor_honrado = min(
            titulo['valor_total'],
            titulo['valor_honrado_oracle'] + valor_manual,
        )
        saldo = max(titulo['valor_total'] - valor_honrado, Decimal('0'))
        vencimento = titulo['data_vencimento']
        dias_vencidos = (
            max((hoje - vencimento).days, 0)
            if vencimento
            else 0
        )
        titulo.update({
            'valor_honrado_manual': valor_manual,
            'valor_total_honrado': valor_honrado,
            'saldo_a_pagar': saldo,
            'dias_vencidos': dias_vencidos,
            'pagamentos': pagamentos,
        })
        codigo = titulo['codigo_fornecedor']
        fornecedor = fornecedores.setdefault(
            codigo,
            {
                'codigo_fornecedor': codigo,
                'nome_fornecedor': titulo['nome_fornecedor'],
                'valor_total': Decimal('0'),
                'valor_total_vencido': Decimal('0'),
                'valor_total_honrado': Decimal('0'),
                'valor_corrente': Decimal('0'),
                'novos_vencidos_7d': Decimal('0'),
                'total_dias_vencidos': 0,
                'dias_atraso': 0,
                'vencimento_mais_antigo': None,
                'titulos_vencidos': 0,
                'titulos_correntes': 0,
                'saldo_a_pagar': Decimal('0'),
                'titulos': [],
            },
        )
        fornecedor['titulos'].append(titulo)
        fornecedor['valor_total'] += titulo['valor_total']
        fornecedor['valor_total_honrado'] += valor_honrado
        fornecedor['saldo_a_pagar'] += saldo
        if saldo > 0:
            fornecedor['total_dias_vencidos'] += dias_vencidos
            fornecedor['dias_atraso'] = max(
                fornecedor['dias_atraso'], dias_vencidos
            )
        if vencimento and vencimento < hoje and saldo > 0:
            fornecedor['valor_total_vencido'] += saldo
            fornecedor['titulos_vencidos'] += 1
            antigo = fornecedor['vencimento_mais_antigo']
            if antigo is None or vencimento < antigo:
                fornecedor['vencimento_mais_antigo'] = vencimento
            if vencimento >= hoje - timedelta(days=6):
                fornecedor['novos_vencidos_7d'] += saldo
        elif saldo > 0:
            fornecedor['valor_corrente'] += saldo
            fornecedor['titulos_correntes'] += 1
    resultado = list(fornecedores.values())
    for fornecedor in resultado:
        fornecedor['titulos'].sort(
            key=lambda titulo: (
                -titulo['dias_vencidos'],
                titulo['data_vencimento'] or date.max,
                titulo['codigo_parcela'],
            )
        )
    return resultado


def _chave_prioridade_fornecedor(item: dict):
    return (
        not item['critico'],
        -item['dias_atraso'],
        -item['valor_total_vencido'],
    )


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
    data_inicio: date | None = None,
    data_fim: date | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    if data_inicio and data_fim and data_inicio > data_fim:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail='A data inicial não pode ser posterior à data final.',
        )
    pagamentos_por_titulo = _pagamentos_manuais(session)
    fornecedores = _consultar_oracle(
        oracle, data_inicio=data_inicio, data_fim=data_fim
    )
    codigos_com_pagamento_manual = sorted({
        int(pagamento['codigo_fornecedor'])
        for pagamentos in pagamentos_por_titulo.values()
        for pagamento in pagamentos
    })
    if codigos_com_pagamento_manual:
        titulos_manuais = _consultar_titulos_oracle(
            oracle,
            codigos_com_pagamento_manual,
            data_inicio=data_inicio,
            data_fim=data_fim,
        )
        fornecedores_manuais = {
            item['codigo_fornecedor']: item
            for item in _agrupar_fornecedores(
                titulos_manuais, pagamentos_por_titulo
            )
        }
        campos_calculados = {
            'valor_total',
            'valor_total_vencido',
            'valor_total_honrado',
            'valor_corrente',
            'novos_vencidos_7d',
            'total_dias_vencidos',
            'dias_atraso',
            'vencimento_mais_antigo',
            'titulos_vencidos',
            'titulos_correntes',
            'saldo_a_pagar',
        }
        for fornecedor in fornecedores:
            calculado = fornecedores_manuais.get(
                fornecedor['codigo_fornecedor']
            )
            if calculado:
                fornecedor.update({
                    campo: calculado[campo]
                    for campo in campos_calculados
                })
    tratamentos = _tratamentos(session)

    def enriquecer(itens):
        for item in itens:
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
                'condicao_negociada': tratamento.get(
                    'condicao_negociada'
                ),
                'observacao': tratamento.get('observacao'),
                'usuario_atualizacao': tratamento.get('usuario_nome'),
                'data_atualizacao': tratamento.get('data_atualizacao'),
            })
            item['saldo_negociar'] = max(
                item['valor_total_vencido']
                - item['pagamento_imediato'],
                Decimal('0'),
            )
            item['valor_vencido'] = item['valor_total_vencido']

    enriquecer(fornecedores)

    vencidos = [item for item in fornecedores if item['saldo_a_pagar'] > 0]
    if not data_inicio and not data_fim:
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
    filtrados.sort(key=_chave_prioridade_fornecedor)
    total = len(filtrados)
    inicio = (page - 1) * page_size
    pagina = filtrados[inicio : inicio + page_size]
    codigos_pagina = [item['codigo_fornecedor'] for item in pagina]
    titulos_pagina = _consultar_titulos_oracle(
        oracle,
        codigos_pagina,
        data_inicio=data_inicio,
        data_fim=data_fim,
    )
    detalhes_pagina = {
        item['codigo_fornecedor']: item['titulos']
        for item in _agrupar_fornecedores(
            titulos_pagina, pagamentos_por_titulo
        )
    }
    for item in pagina:
        item['titulos'] = detalhes_pagina.get(
            item['codigo_fornecedor'], []
        )

    def serializar(item):
        titulos_serializados = []
        for titulo in item['titulos']:
            pagamentos = [
                {
                    **pagamento,
                    'data_pagamento': pagamento['data_pagamento'].isoformat(),
                    'valor_pago': _serializar_decimal(
                        pagamento['valor_pago']
                    ),
                    'data_criacao': pagamento['data_criacao'].isoformat(),
                    'data_atualizacao': pagamento[
                        'data_atualizacao'
                    ].isoformat(),
                }
                for pagamento in titulo['pagamentos']
            ]
            titulos_serializados.append({
                **titulo,
                'data_vencimento': titulo['data_vencimento'].isoformat()
                if titulo['data_vencimento']
                else None,
                'data_lancamento': titulo['data_lancamento'].isoformat()
                if titulo['data_lancamento']
                else None,
                'data_emissao': titulo['data_emissao'].isoformat()
                if titulo['data_emissao']
                else None,
                'valor_total': _serializar_decimal(titulo['valor_total']),
                'valor_honrado_oracle': _serializar_decimal(
                    titulo['valor_honrado_oracle']
                ),
                'valor_honrado_manual': _serializar_decimal(
                    titulo['valor_honrado_manual']
                ),
                'valor_total_honrado': _serializar_decimal(
                    titulo['valor_total_honrado']
                ),
                'saldo_a_pagar': _serializar_decimal(
                    titulo['saldo_a_pagar']
                ),
                'pagamentos': pagamentos,
            })
        return {
            **item,
            'valor_total': _serializar_decimal(item['valor_total']),
            'valor_total_vencido': _serializar_decimal(
                item['valor_total_vencido']
            ),
            'valor_total_honrado': _serializar_decimal(
                item['valor_total_honrado']
            ),
            'saldo_a_pagar': _serializar_decimal(item['saldo_a_pagar']),
            'valor_vencido': _serializar_decimal(item['valor_vencido']),
            'valor_corrente': _serializar_decimal(item['valor_corrente']),
            'novos_vencidos_7d': _serializar_decimal(
                item['novos_vencidos_7d']
            ),
            'pagamento_imediato': _serializar_decimal(
                item['pagamento_imediato']
            ),
            'saldo_negociar': _serializar_decimal(item['saldo_negociar']),
            'titulos': titulos_serializados,
        }

    return {
        'fornecedores': [
            serializar(item) for item in pagina
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


@router.patch('/fornecedores/{codigo_fornecedor}/criticidade')
def atualizar_criticidade_fornecedor(
    codigo_fornecedor: int,
    payload: CriticidadeFornecedorInput,
    usuario: UsuarioAtual,
    session: SessionPostgres,
):
    session.execute(
        text("""
        INSERT INTO api_prontocardio.contas_pagar_tratamentos
            (codigo_fornecedor, critico, usuario_id)
        VALUES (:codigo, :critico, :usuario_id)
        ON CONFLICT (codigo_fornecedor) DO UPDATE SET
            critico = EXCLUDED.critico,
            usuario_id = EXCLUDED.usuario_id,
            data_atualizacao = timezone('America/Sao_Paulo', now())
    """),
        {
            'codigo': codigo_fornecedor,
            'critico': payload.critico,
            'usuario_id': usuario.id,
        },
    )
    session.commit()
    return {
        'detail': (
            'Fornecedor marcado como crítico.'
            if payload.critico
            else 'Criticidade removida do fornecedor.'
        )
    }


@router.post(
    '/fornecedores/{codigo_fornecedor}/titulos/{codigo_parcela}/pagamentos',
    status_code=HTTPStatus.CREATED,
)
def registrar_pagamento_titulo(
    codigo_fornecedor: int,
    codigo_parcela: int,
    payload: PagamentoTituloInput,
    usuario: UsuarioAtual,
    session: SessionPostgres,
):
    pagamento_id = session.execute(
        text("""
        INSERT INTO api_prontocardio.contas_pagar_pagamentos
            (codigo_fornecedor, codigo_parcela, data_pagamento, valor_pago,
             banco, agencia, numero_conta, observacao, usuario_id)
        VALUES (:fornecedor, :parcela, :data_pagamento, :valor_pago,
                :banco, :agencia, :numero_conta, :observacao, :usuario_id)
        RETURNING id
    """),
        {
            'fornecedor': codigo_fornecedor,
            'parcela': codigo_parcela,
            'data_pagamento': payload.data_pagamento,
            'valor_pago': payload.valor_pago,
            'banco': payload.banco,
            'agencia': payload.agencia,
            'numero_conta': payload.numero_conta,
            'observacao': payload.observacao,
            'usuario_id': usuario.id,
        },
    ).scalar_one()
    session.commit()
    return {
        'id': pagamento_id,
        'detail': 'Pagamento informado com sucesso.',
    }


@router.put(
    '/fornecedores/{codigo_fornecedor}/titulos/{codigo_parcela}'
    '/pagamentos/{pagamento_id}'
)
def atualizar_pagamento_titulo(
    codigo_fornecedor: int,
    codigo_parcela: int,
    pagamento_id: int,
    payload: PagamentoTituloInput,
    usuario: UsuarioAtual,
    session: SessionPostgres,
):
    atualizado = session.execute(
        text("""
        UPDATE api_prontocardio.contas_pagar_pagamentos
           SET data_pagamento = :data_pagamento,
               valor_pago = :valor_pago,
               banco = :banco,
               agencia = :agencia,
               numero_conta = :numero_conta,
               observacao = :observacao,
               usuario_id = :usuario_id,
               data_atualizacao = timezone('America/Sao_Paulo', now())
         WHERE id = :pagamento_id
           AND codigo_fornecedor = :fornecedor
           AND codigo_parcela = :parcela
     RETURNING id
    """),
        {
            'pagamento_id': pagamento_id,
            'fornecedor': codigo_fornecedor,
            'parcela': codigo_parcela,
            'data_pagamento': payload.data_pagamento,
            'valor_pago': payload.valor_pago,
            'banco': payload.banco,
            'agencia': payload.agencia,
            'numero_conta': payload.numero_conta,
            'observacao': payload.observacao,
            'usuario_id': usuario.id,
        },
    ).scalar_one_or_none()
    if atualizado is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Pagamento não encontrado para este título.',
        )
    session.commit()
    return {'detail': 'Pagamento atualizado com sucesso.'}


@router.delete(
    '/fornecedores/{codigo_fornecedor}/titulos/{codigo_parcela}'
    '/pagamentos/{pagamento_id}',
    status_code=HTTPStatus.NO_CONTENT,
)
def excluir_pagamento_titulo(
    codigo_fornecedor: int,
    codigo_parcela: int,
    pagamento_id: int,
    _: UsuarioAtual,
    session: SessionPostgres,
):
    excluido = session.execute(
        text("""
        DELETE FROM api_prontocardio.contas_pagar_pagamentos
         WHERE id = :pagamento_id
           AND codigo_fornecedor = :fornecedor
           AND codigo_parcela = :parcela
     RETURNING id
    """),
        {
            'pagamento_id': pagamento_id,
            'fornecedor': codigo_fornecedor,
            'parcela': codigo_parcela,
        },
    ).scalar_one_or_none()
    if excluido is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail='Pagamento não encontrado para este título.',
        )
    session.commit()
    return Response(status_code=HTTPStatus.NO_CONTENT)
