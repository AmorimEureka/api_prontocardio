"""use HPC views for convenios and bank accounts

Revision ID: 20260712_012
Revises: 20260712_011
Create Date: 2026-07-12 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260712_012'
down_revision: Union[str, Sequence[str], None] = '20260712_011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
FK_EXTRATO_CONTA = 'lancamentos_extrato_bancario_conta_bancaria_id_fkey'
FK_CONCILIACAO_CONTA = 'conciliacoes_faturamento_conta_bancaria_id_fkey'


def upgrade() -> None:
    op.drop_constraint(
        FK_EXTRATO_CONTA,
        'lancamentos_extrato_bancario',
        schema=SCHEMA_NAME,
        type_='foreignkey',
    )
    op.drop_constraint(
        FK_CONCILIACAO_CONTA,
        'conciliacoes_faturamento',
        schema=SCHEMA_NAME,
        type_='foreignkey',
    )
    op.drop_table('contas_bancarias_recebimento', schema=SCHEMA_NAME)


def downgrade() -> None:
    op.create_table(
        'contas_bancarias_recebimento',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('banco', sa.String(), nullable=False),
        sa.Column('agencia', sa.String(), nullable=False),
        sa.Column('conta', sa.String(), nullable=False),
        sa.Column('digito', sa.String(), nullable=True),
        sa.Column('descricao', sa.String(), nullable=True),
        sa.Column(
            'ativo',
            sa.Boolean(),
            server_default=sa.text('true'),
            nullable=False,
        ),
        sa.Column(
            'data_criacao',
            sa.DateTime(),
            server_default=sa.text("timezone('America/Sao_Paulo', now())"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        schema=SCHEMA_NAME,
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {SCHEMA_NAME}.contas_bancarias_recebimento
                (id, banco, agencia, conta, descricao)
            SELECT DISTINCT conta_bancaria_id,
                'Conta Oracle', '-', conta_bancaria_id::text,
                'Registro restaurado pelo downgrade'
            FROM (
                SELECT conta_bancaria_id
                FROM {SCHEMA_NAME}.lancamentos_extrato_bancario
                UNION
                SELECT conta_bancaria_id
                FROM {SCHEMA_NAME}.conciliacoes_faturamento
            ) contas
            WHERE conta_bancaria_id IS NOT NULL
            """
        )
    )
    op.create_foreign_key(
        FK_EXTRATO_CONTA,
        'lancamentos_extrato_bancario',
        'contas_bancarias_recebimento',
        ['conta_bancaria_id'],
        ['id'],
        source_schema=SCHEMA_NAME,
        referent_schema=SCHEMA_NAME,
    )
    op.create_foreign_key(
        FK_CONCILIACAO_CONTA,
        'conciliacoes_faturamento',
        'contas_bancarias_recebimento',
        ['conta_bancaria_id'],
        ['id'],
        source_schema=SCHEMA_NAME,
        referent_schema=SCHEMA_NAME,
    )
