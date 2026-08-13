"""normalize denial code and preserve Oracle matching criterion

Revision ID: 20260811_039
Revises: 20260809_038
Create Date: 2026-08-11 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260811_039'
down_revision: Union[str, Sequence[str], None] = '20260809_038'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
TABLE_NAME = 'registros_glosa'
UNIQUE_NAME = 'uq_registro_glosa_conciliacao_item'
CHECK_NAME = 'ck_registros_glosa_motivo_codigo'


def upgrade() -> None:
    op.add_column(
        TABLE_NAME,
        sa.Column('cd_tuss', sa.String(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.add_column(
        'registros_glosa_demonstrativo_ipm',
        sa.Column(
            'criterio_correspondencia',
            sa.String(length=80),
            nullable=True,
        ),
        schema=SCHEMA_NAME,
    )
    op.alter_column(
        TABLE_NAME,
        'motivo_glosa',
        existing_type=sa.String(),
        nullable=True,
        schema=SCHEMA_NAME,
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA_NAME}.{TABLE_NAME} AS rg
               SET motivo_glosa = origem.codigo_glosa
              FROM (
                    SELECT rastreio.registro_glosa_id,
                           MIN(NULLIF(BTRIM(demo.codigo_glosa), ''))
                               AS codigo_glosa
                      FROM {SCHEMA_NAME}.registros_glosa_demonstrativo_ipm
                               AS rastreio
                      JOIN {SCHEMA_NAME}.demonstrativo_conta_ipm AS demo
                        ON demo.id_registro = rastreio.id_registro
                     GROUP BY rastreio.registro_glosa_id
                   ) AS origem
             WHERE origem.registro_glosa_id = rg.id
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA_NAME}.{TABLE_NAME}
               SET motivo_glosa = CASE
                    WHEN motivo_glosa ~ '^\\s*[0-9]+'
                    THEN SUBSTRING(motivo_glosa FROM '^\\s*([0-9]+)')
                    ELSE NULL
                   END
             WHERE motivo_glosa IS NOT NULL
               AND motivo_glosa !~ '^[0-9]+$'
            """
        )
    )
    op.drop_constraint(
        UNIQUE_NAME,
        TABLE_NAME,
        schema=SCHEMA_NAME,
        type_='unique',
    )
    op.create_unique_constraint(
        UNIQUE_NAME,
        TABLE_NAME,
        [
            'conciliacao_remessa_id',
            'conta',
            'cd_lancamento',
            'motivo_glosa',
            'sn_glosado',
        ],
        schema=SCHEMA_NAME,
    )
    op.create_check_constraint(
        CHECK_NAME,
        TABLE_NAME,
        "motivo_glosa IS NULL OR motivo_glosa ~ '^[0-9]+$'",
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_constraint(
        CHECK_NAME,
        TABLE_NAME,
        schema=SCHEMA_NAME,
        type_='check',
    )
    op.drop_constraint(
        UNIQUE_NAME,
        TABLE_NAME,
        schema=SCHEMA_NAME,
        type_='unique',
    )
    op.create_unique_constraint(
        UNIQUE_NAME,
        TABLE_NAME,
        ['conciliacao_remessa_id', 'conta', 'cd_lancamento', 'sn_glosado'],
        schema=SCHEMA_NAME,
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA_NAME}.{TABLE_NAME}
               SET motivo_glosa = 'Glosa sem codigo informado'
             WHERE motivo_glosa IS NULL
            """
        )
    )
    op.alter_column(
        TABLE_NAME,
        'motivo_glosa',
        existing_type=sa.String(),
        nullable=False,
        schema=SCHEMA_NAME,
    )
    op.drop_column(
        'registros_glosa_demonstrativo_ipm',
        'criterio_correspondencia',
        schema=SCHEMA_NAME,
    )
    op.drop_column(TABLE_NAME, 'cd_tuss', schema=SCHEMA_NAME)
