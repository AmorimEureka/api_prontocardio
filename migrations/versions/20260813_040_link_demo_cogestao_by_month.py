"""link IPM demonstratives to cogestao processes by protocol and month

Revision ID: 20260813_040
Revises: 20260811_039
Create Date: 2026-08-13 13:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = '20260813_040'
down_revision: Union[str, Sequence[str], None] = '20260811_039'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
VIEW_NAME = 'demonstrativo_processos_ipm'
DEMO_INDEX = 'ix_demo_ipm_protocolo_data_valor'
COG_NR_INDEX = 'ix_cog_ipm_nr_competencia_valor'
COG_ORIGEM_INDEX = 'ix_cog_ipm_nr_origem_competencia_valor'


def upgrade() -> None:
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS {DEMO_INDEX}
            ON {SCHEMA_NAME}.demonstrativo_conta_ipm
            (UPPER(BTRIM(numero_protocolo)), data_realizacao,
             valor_protocolo)
    """)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS {COG_NR_INDEX}
            ON {SCHEMA_NAME}.processos_ipm_saude_cogestao
            (UPPER(BTRIM(nr)), competencia_producao, valor_protocolo)
    """)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS {COG_ORIGEM_INDEX}
            ON {SCHEMA_NAME}.processos_ipm_saude_cogestao
            (UPPER(BTRIM(nr_origem)), competencia_producao, valor_protocolo)
    """)
    op.execute(f"""
        CREATE VIEW {SCHEMA_NAME}.{VIEW_NAME} AS
        WITH aliases AS (
            SELECT UPPER(BTRIM(protocolo)) AS numero_protocolo,
                   BTRIM(competencia_producao) AS competencia_producao,
                   numero_processo,
                   valor_protocolo,
                   MIN(id_registro) AS cogestao_id_registro,
                   STRING_AGG(DISTINCT origem, ',' ORDER BY origem)
                       AS origem_protocolo
              FROM {SCHEMA_NAME}.processos_ipm_saude_cogestao
              CROSS JOIN LATERAL (
                    VALUES (nr, 'nr'), (nr_origem, 'nr_origem')
              ) AS protocolo_alias(protocolo, origem)
             WHERE NULLIF(BTRIM(protocolo), '') IS NOT NULL
               AND NULLIF(BTRIM(competencia_producao), '') IS NOT NULL
               AND valor_protocolo IS NOT NULL
             GROUP BY UPPER(BTRIM(protocolo)),
                      BTRIM(competencia_producao), numero_processo,
                      valor_protocolo
        ), candidatos AS (
            SELECT demo.id_registro AS demonstrativo_id_registro,
                   demo.numero_protocolo,
                   TO_CHAR(demo.data_realizacao, 'MM/YYYY')
                       AS competencia_realizacao,
                   demo.valor_protocolo AS valor_protocolo_mes,
                   cog.cogestao_id_registro,
                   cog.numero_processo,
                   cog.competencia_producao,
                   cog.valor_protocolo AS valor_protocolo_cogestao,
                   cog.origem_protocolo,
                   COUNT(*) OVER (PARTITION BY demo.id_registro)
                       AS quantidade_candidatos,
                   COUNT(*) FILTER (
                       WHERE ROUND(cog.valor_protocolo, 2)
                           = ROUND(demo.valor_protocolo, 2)
                   ) OVER (PARTITION BY demo.id_registro)
                       AS quantidade_candidatos_valor
              FROM {SCHEMA_NAME}.demonstrativo_conta_ipm AS demo
              JOIN aliases AS cog
                ON cog.numero_protocolo
                 = UPPER(BTRIM(demo.numero_protocolo))
               AND cog.competencia_producao
                 = TO_CHAR(demo.data_realizacao, 'MM/YYYY')
             WHERE demo.data_realizacao IS NOT NULL
               AND NULLIF(BTRIM(demo.numero_protocolo), '') IS NOT NULL
        )
        SELECT demonstrativo_id_registro,
               cogestao_id_registro,
               numero_processo,
               numero_protocolo,
               competencia_realizacao,
               competencia_producao,
               valor_protocolo_mes,
               valor_protocolo_cogestao,
               origem_protocolo,
               CASE
                   WHEN quantidade_candidatos = 1
                   THEN 'protocolo_competencia'
                   ELSE 'protocolo_competencia_valor'
               END AS criterio_associacao
          FROM candidatos
         WHERE quantidade_candidatos = 1
            OR (
                quantidade_candidatos_valor = 1
                AND ROUND(valor_protocolo_cogestao, 2)
                    = ROUND(valor_protocolo_mes, 2)
            )
    """)


def downgrade() -> None:
    op.execute(f'DROP VIEW IF EXISTS {SCHEMA_NAME}.{VIEW_NAME}')
    op.execute(f'DROP INDEX IF EXISTS {SCHEMA_NAME}.{COG_ORIGEM_INDEX}')
    op.execute(f'DROP INDEX IF EXISTS {SCHEMA_NAME}.{COG_NR_INDEX}')
    op.execute(f'DROP INDEX IF EXISTS {SCHEMA_NAME}.{DEMO_INDEX}')
