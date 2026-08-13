"""expand the IPM demonstrative-process association view

Revision ID: 20260813_041
Revises: 20260813_040
Create Date: 2026-08-13 14:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = '20260813_041'
down_revision: Union[str, Sequence[str], None] = '20260813_040'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
VIEW_NAME = 'demonstrativo_processos_ipm'


def upgrade() -> None:
    op.execute(f'DROP VIEW {SCHEMA_NAME}.{VIEW_NAME}')
    op.execute(f"""
        CREATE VIEW {SCHEMA_NAME}.{VIEW_NAME} AS
        WITH aliases AS (
            SELECT UPPER(BTRIM(protocolo)) AS protocolo_normalizado,
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
                   cog.*,
                   ROUND(cog.valor_protocolo, 2)
                       = ROUND(demo.valor_protocolo, 2) AS valor_coincidente
              FROM {SCHEMA_NAME}.demonstrativo_conta_ipm AS demo
              JOIN aliases AS cog
                ON cog.protocolo_normalizado
                 = UPPER(BTRIM(demo.numero_protocolo))
               AND cog.competencia_producao
                 = TO_CHAR(demo.data_realizacao, 'MM/YYYY')
             WHERE demo.data_realizacao IS NOT NULL
               AND NULLIF(BTRIM(demo.numero_protocolo), '') IS NOT NULL
        ), estatisticas AS (
            SELECT demonstrativo_id_registro,
                   COUNT(*) AS quantidade_candidatos,
                   COUNT(*) FILTER (WHERE valor_coincidente)
                       AS quantidade_candidatos_valor,
                   JSONB_AGG(
                       JSONB_BUILD_OBJECT(
                           'numero_processo', numero_processo,
                           'competencia_producao', competencia_producao,
                           'valor_protocolo', valor_protocolo,
                           'origem_protocolo', origem_protocolo
                       ) ORDER BY numero_processo, valor_protocolo
                   ) AS candidatos_associacao
              FROM candidatos
             GROUP BY demonstrativo_id_registro
        ), resolvidos AS (
            SELECT DISTINCT ON (cand.demonstrativo_id_registro)
                   cand.demonstrativo_id_registro,
                   cand.cogestao_id_registro,
                   cand.numero_processo,
                   cand.competencia_producao,
                   cand.valor_protocolo AS valor_protocolo_cogestao,
                   cand.origem_protocolo,
                   CASE
                       WHEN est.quantidade_candidatos = 1
                       THEN 'ASSOCIADO_PROTOCOLO_COMPETENCIA'
                       ELSE 'ASSOCIADO_PROTOCOLO_COMPETENCIA_VALOR'
                   END AS status_associacao
              FROM candidatos AS cand
              JOIN estatisticas AS est
                USING (demonstrativo_id_registro)
             WHERE est.quantidade_candidatos = 1
                OR (
                    est.quantidade_candidatos_valor = 1
                    AND cand.valor_coincidente
                )
             ORDER BY cand.demonstrativo_id_registro,
                      cand.numero_processo, cand.valor_protocolo
        )
        SELECT demo.id_registro,
               demo.referencia,
               demo.cnpj_operadora,
               demo.numero_lote,
               demo.data_envio_lote,
               demo.numero_protocolo,
               demo.valor_protocolo,
               demo.valor_glosa_protocolo,
               demo.numero_guia_senha,
               demo.data_realizacao,
               demo.descricao_servico,
               demo.codigo_tabela,
               demo.codigo_servico,
               demo.grau_participacao,
               demo.quantidade_executada,
               demo.valor_processado,
               demo.valor_liberado,
               demo.valor_glosa,
               demo.codigo_glosa,
               demo.nome_beneficiario,
               demo.codigo_beneficiario,
               demo.valor_protocolo AS valor_protocolo_mes,
               res.cogestao_id_registro,
               res.numero_processo,
               res.competencia_producao,
               res.valor_protocolo_cogestao,
               res.origem_protocolo,
               CASE
                   WHEN res.status_associacao IS NOT NULL
                   THEN res.status_associacao
                   WHEN est.quantidade_candidatos IS NULL
                   THEN 'SEM_PROCESSO'
                   ELSE 'AMBIGUO'
               END AS status_associacao,
               COALESCE(est.candidatos_associacao, '[]'::JSONB)
                   AS candidatos_associacao
          FROM {SCHEMA_NAME}.demonstrativo_conta_ipm AS demo
          LEFT JOIN estatisticas AS est
            ON est.demonstrativo_id_registro = demo.id_registro
          LEFT JOIN resolvidos AS res
            ON res.demonstrativo_id_registro = demo.id_registro
    """)


def downgrade() -> None:
    op.execute(f'DROP VIEW {SCHEMA_NAME}.{VIEW_NAME}')
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
