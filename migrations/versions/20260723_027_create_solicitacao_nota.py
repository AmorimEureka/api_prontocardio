"""create solicitacao nota

Revision ID: 20260723_027
Revises: 20260722_026
Create Date: 2026-07-23 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260723_027'
down_revision: Union[str, Sequence[str], None] = '20260722_026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'


def upgrade() -> None:
    op.create_table(
        'solicitacao_nota',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('codigo_atendimento', sa.Integer(), nullable=False),
        sa.Column('codigo_paciente', sa.Integer(), nullable=False),
        sa.Column('codigo_convenio', sa.Integer(), nullable=False),
        sa.Column('nm_paciente', sa.String(length=200), nullable=False),
        sa.Column('convenio', sa.String(length=100), nullable=False),
        sa.Column('local', sa.String(length=20), nullable=False),
        sa.Column('procedimento', sa.String(length=500), nullable=False),
        sa.Column('tipo_atendimento', sa.String(length=50), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('nr_cpf', sa.String(length=20), nullable=True),
        sa.Column('nr_cep', sa.String(length=20), nullable=True),
        sa.Column('ds_endereco', sa.String(length=200), nullable=True),
        sa.Column('nr_endereco', sa.String(length=30), nullable=True),
        sa.Column('nm_bairro', sa.String(length=100), nullable=True),
        sa.Column('ds_complemento', sa.String(length=100), nullable=True),
        sa.Column('email', sa.String(length=150), nullable=True),
        sa.Column('nr_fone', sa.String(length=50), nullable=True),
        sa.Column(
            'data_criacao',
            sa.DateTime(),
            server_default=sa.text(
                "timezone('America/Sao_Paulo', now())"
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "local IN ('Clinica 1', 'Clinica 2', 'Emergencia')",
            name='ck_solicitacao_nota_local',
        ),
        sa.ForeignKeyConstraint(
            ['usuario_id'],
            [f'{SCHEMA_NAME}.usuarios_api.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
        schema=SCHEMA_NAME,
    )
    op.create_index(
        'ix_solicitacao_nota_codigo_atendimento',
        'solicitacao_nota',
        ['codigo_atendimento'],
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_index(
        'ix_solicitacao_nota_codigo_atendimento',
        table_name='solicitacao_nota',
        schema=SCHEMA_NAME,
    )
    op.drop_table('solicitacao_nota', schema=SCHEMA_NAME)
