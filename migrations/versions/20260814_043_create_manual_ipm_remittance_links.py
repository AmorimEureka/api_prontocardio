"""create manual IPM process-remittance links

Revision ID: 20260814_043
Revises: 20260813_042
Create Date: 2026-08-14 14:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260814_043'
down_revision: Union[str, Sequence[str], None] = '20260813_042'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
TABLE_NAME = 'associacoes_remessas_ipm_manuais'


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('numero_processo', sa.String(), nullable=False),
        sa.Column('competencia_producao', sa.String(7), nullable=False),
        sa.Column('nr', sa.String(), nullable=False),
        sa.Column('cd_remessa', sa.BigInteger(), nullable=False),
        sa.Column(
            'usuario_id',
            sa.Integer(),
            sa.ForeignKey(
                f'{SCHEMA_NAME}.usuarios_api.id', ondelete='RESTRICT'
            ),
            nullable=False,
        ),
        sa.Column(
            'data_criacao',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text(
                "timezone('America/Sao_Paulo', now())"
            ),
        ),
        sa.Column(
            'data_atualizacao',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text(
                "timezone('America/Sao_Paulo', now())"
            ),
        ),
        sa.UniqueConstraint(
            'numero_processo',
            'competencia_producao',
            'nr',
            name='uq_assoc_remessa_ipm_manual_processo_nr',
        ),
        sa.UniqueConstraint(
            'cd_remessa', name='uq_assoc_remessa_ipm_manual_remessa'
        ),
        schema=SCHEMA_NAME,
    )
    op.create_index(
        'ix_assoc_remessa_ipm_manual_competencia',
        TABLE_NAME,
        ['competencia_producao'],
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_table(TABLE_NAME, schema=SCHEMA_NAME)
