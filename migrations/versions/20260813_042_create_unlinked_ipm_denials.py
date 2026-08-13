"""create storage for unlinked IPM denial items

Revision ID: 20260813_042
Revises: 20260813_041
Create Date: 2026-08-13 15:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260813_042'
down_revision: Union[str, Sequence[str], None] = '20260813_041'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
TABLE_NAME = 'glossas_nao_vinculadas_ipm'


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column('id_registro', sa.String(), primary_key=True),
        sa.Column('numero_processo', sa.String()),
        sa.Column('cd_remessa', sa.BigInteger()),
        sa.Column('motivo', sa.String(40), nullable=False),
        sa.Column('criterio_correspondencia', sa.String(80)),
        sa.Column('remessas_candidatas', sa.JSON(), nullable=False),
        sa.Column('numero_protocolo', sa.String()),
        sa.Column('data_realizacao', sa.Date()),
        sa.Column('numero_guia_senha', sa.String()),
        sa.Column('codigo_servico', sa.String()),
        sa.Column('codigo_beneficiario', sa.String()),
        sa.Column('codigo_glosa', sa.String()),
        sa.Column('valor_processado', sa.Numeric(18, 2)),
        sa.Column('valor_glosa', sa.Numeric(18, 2), nullable=False),
        sa.Column(
            'data_primeira_ocorrencia', sa.DateTime(), nullable=False,
            server_default=sa.text(
                "timezone('America/Sao_Paulo', now())"
            ),
        ),
        sa.Column(
            'data_ultima_tentativa', sa.DateTime(), nullable=False,
            server_default=sa.text(
                "timezone('America/Sao_Paulo', now())"
            ),
        ),
        schema=SCHEMA_NAME,
    )
    op.create_index(
        'ix_glossas_nao_vinculadas_ipm_remessa', TABLE_NAME,
        ['cd_remessa'], schema=SCHEMA_NAME,
    )
    op.create_index(
        'ix_glossas_nao_vinculadas_ipm_processo', TABLE_NAME,
        ['numero_processo'], schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_table(TABLE_NAME, schema=SCHEMA_NAME)
