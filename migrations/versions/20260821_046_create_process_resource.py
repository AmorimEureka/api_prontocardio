"""create process-level appeal registration

Revision ID: 20260821_046
Revises: 20260814_045
Create Date: 2026-08-21 15:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260821_046'
down_revision: Union[str, Sequence[str], None] = '20260814_045'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
TABLE_NAME = 'processos_recurso_glosa'


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('processo_original', sa.String(100), nullable=False),
        sa.Column(
            'processo_original_normalizado', sa.String(100), nullable=False
        ),
        sa.Column('processo_recurso', sa.String(100), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=True),
        sa.Column(
            'data_criacao',
            sa.DateTime(),
            server_default=sa.text(
                "timezone('America/Sao_Paulo', now())"
            ),
            nullable=False,
        ),
        sa.Column(
            'data_atualizacao',
            sa.DateTime(),
            server_default=sa.text(
                "timezone('America/Sao_Paulo', now())"
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['usuario_id'],
            [f'{SCHEMA_NAME}.usuarios_api.id'],
            ondelete='SET NULL',
        ),
        sa.UniqueConstraint(
            'processo_original_normalizado',
            name='uq_processo_recurso_glosa_original_normalizado',
        ),
        schema=SCHEMA_NAME,
    )
    op.execute(
        f"""
        INSERT INTO {SCHEMA_NAME}.{TABLE_NAME} (
            processo_original,
            processo_original_normalizado,
            processo_recurso,
            data_criacao,
            data_atualizacao
        )
        SELECT processo_original,
               lower(processo_original),
               processo_recurso,
               data_criacao,
               data_criacao
          FROM (
              SELECT trim(processo_controle_fatura_gab) processo_original,
                     trim(processo_recurso) processo_recurso,
                     data_criacao,
                     row_number() OVER (
                         PARTITION BY lower(
                             trim(processo_controle_fatura_gab)
                         )
                         ORDER BY data_criacao DESC, id DESC
                     ) ordem
                FROM {SCHEMA_NAME}.registros_glosa
               WHERE trim(coalesce(processo_controle_fatura_gab, '')) <> ''
                 AND trim(coalesce(processo_recurso, '')) <> ''
                 AND sn_ativo = 'true'
          ) legado
         WHERE ordem = 1
        """
    )
    op.execute(
        f"""
        UPDATE {SCHEMA_NAME}.usuarios_api
           SET telas_permitidas = (
               telas_permitidas::jsonb || '["recursos_processos"]'::jsonb
           )::json
         WHERE telas_permitidas::jsonb ? 'follow_up_glosas'
           AND NOT (telas_permitidas::jsonb ? 'recursos_processos')
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE {SCHEMA_NAME}.usuarios_api
           SET telas_permitidas = (
               SELECT coalesce(json_agg(valor), '[]'::json)
                 FROM json_array_elements_text(telas_permitidas) valor
                WHERE valor <> 'recursos_processos'
           )
        """
    )
    op.drop_table(TABLE_NAME, schema=SCHEMA_NAME)
