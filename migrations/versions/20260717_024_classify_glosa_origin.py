"""classify legacy and reconciliation denial records

Revision ID: 20260717_024
Revises: 20260717_023
Create Date: 2026-07-17 17:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260717_024'
down_revision: Union[str, Sequence[str], None] = '20260717_023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
ORIGIN_CONSTRAINT = 'ck_registros_glosa_origem'
LINK_CONSTRAINT = 'ck_registros_glosa_origem_vinculo'


def upgrade() -> None:
    op.add_column(
        'registros_glosa',
        sa.Column(
            'origem_registro',
            sa.String(length=20),
            server_default=sa.text("'triagem'"),
            nullable=False,
        ),
        schema=SCHEMA_NAME,
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA_NAME}.registros_glosa
               SET origem_registro = CASE
                   WHEN conciliacao_remessa_id IS NULL THEN 'triagem'
                   ELSE 'conciliacao'
               END
            """
        )
    )
    op.create_check_constraint(
        ORIGIN_CONSTRAINT,
        'registros_glosa',
        "origem_registro IN ('triagem', 'conciliacao')",
        schema=SCHEMA_NAME,
    )
    op.create_check_constraint(
        LINK_CONSTRAINT,
        'registros_glosa',
        'conciliacao_remessa_id IS NULL OR '
        "origem_registro = 'conciliacao'",
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_constraint(
        LINK_CONSTRAINT,
        'registros_glosa',
        schema=SCHEMA_NAME,
        type_='check',
    )
    op.drop_constraint(
        ORIGIN_CONSTRAINT,
        'registros_glosa',
        schema=SCHEMA_NAME,
        type_='check',
    )
    op.drop_column(
        'registros_glosa',
        'origem_registro',
        schema=SCHEMA_NAME,
    )
