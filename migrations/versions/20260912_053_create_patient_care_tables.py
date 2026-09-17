"""create patient care and post-discharge tables

Revision ID: 20260912_053
Revises: 20260910_052
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260912_053'
down_revision: Union[str, Sequence[str], None] = '20260910_052'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'


def upgrade() -> None:
    op.create_table(
        'respostas_pos_alta',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('chave_idempotencia', sa.String(64), nullable=False),
        sa.Column('cd_paciente', sa.Integer(), nullable=False),
        sa.Column('cd_atendimento', sa.Integer(), nullable=False),
        sa.Column('etapa', sa.String(30), nullable=False),
        sa.Column('respostas', sa.JSON(), nullable=False),
        sa.Column('resultado', sa.String(30), nullable=False),
        sa.Column(
            'data_criacao',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            'cd_paciente',
            'chave_idempotencia',
            name='uq_respostas_pos_alta_paciente_chave',
        ),
        schema=SCHEMA_NAME,
    )
    op.create_index(
        'ix_respostas_pos_alta_cd_paciente',
        'respostas_pos_alta',
        ['cd_paciente'],
        schema=SCHEMA_NAME,
    )
    op.create_index(
        'ix_respostas_pos_alta_cd_atendimento',
        'respostas_pos_alta',
        ['cd_atendimento'],
        schema=SCHEMA_NAME,
    )
    op.create_table(
        'solicitacoes_paciente',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('chave_idempotencia', sa.String(64), nullable=False),
        sa.Column('cd_paciente', sa.Integer(), nullable=False),
        sa.Column('tipo', sa.String(40), nullable=False),
        sa.Column('destino', sa.String(30), nullable=False),
        sa.Column('assunto', sa.String(300), nullable=False),
        sa.Column('contexto', sa.JSON(), nullable=False),
        sa.Column(
            'status',
            sa.String(20),
            nullable=False,
            server_default='pendente',
        ),
        sa.Column(
            'data_criacao',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            'cd_paciente',
            'chave_idempotencia',
            name='uq_solicitacoes_paciente_chave',
        ),
        schema=SCHEMA_NAME,
    )
    op.create_index(
        'ix_solicitacoes_paciente_cd_paciente',
        'solicitacoes_paciente',
        ['cd_paciente'],
        schema=SCHEMA_NAME,
    )
    op.create_index(
        'ix_solicitacoes_paciente_destino',
        'solicitacoes_paciente',
        ['destino'],
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_index(
        'ix_solicitacoes_paciente_destino',
        table_name='solicitacoes_paciente',
        schema=SCHEMA_NAME,
    )
    op.drop_index(
        'ix_solicitacoes_paciente_cd_paciente',
        table_name='solicitacoes_paciente',
        schema=SCHEMA_NAME,
    )
    op.drop_table('solicitacoes_paciente', schema=SCHEMA_NAME)
    op.drop_index(
        'ix_respostas_pos_alta_cd_atendimento',
        table_name='respostas_pos_alta',
        schema=SCHEMA_NAME,
    )
    op.drop_index(
        'ix_respostas_pos_alta_cd_paciente',
        table_name='respostas_pos_alta',
        schema=SCHEMA_NAME,
    )
    op.drop_table('respostas_pos_alta', schema=SCHEMA_NAME)
