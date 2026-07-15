"""create conciliacao faturamento tables

Revision ID: 20260712_011
Revises: 20260702_010
Create Date: 2026-07-12 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260712_011'
down_revision: Union[str, Sequence[str], None] = '20260702_010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'


def upgrade() -> None:
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
    op.create_table(
        'lancamentos_extrato_bancario',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conta_bancaria_id', sa.Integer(), nullable=False),
        sa.Column('data_lancamento', sa.Date(), nullable=False),
        sa.Column('valor', sa.Numeric(14, 2), nullable=False),
        sa.Column('descricao', sa.String(), nullable=True),
        sa.Column('documento', sa.String(), nullable=True),
        sa.Column(
            'conciliado',
            sa.Boolean(),
            server_default=sa.text('false'),
            nullable=False,
        ),
        sa.Column(
            'data_criacao',
            sa.DateTime(),
            server_default=sa.text("timezone('America/Sao_Paulo', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['conta_bancaria_id'],
            [f'{SCHEMA_NAME}.contas_bancarias_recebimento.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
        schema=SCHEMA_NAME,
    )
    op.create_index(
        'ix_lancamentos_extrato_conta_data',
        'lancamentos_extrato_bancario',
        ['conta_bancaria_id', 'data_lancamento'],
        schema=SCHEMA_NAME,
    )
    op.create_table(
        'conciliacoes_faturamento',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nfse_row_hash', sa.String(), nullable=False),
        sa.Column('numero_nfse', sa.String(), nullable=False),
        sa.Column('cnpj_convenio', sa.String(), nullable=False),
        sa.Column('convenio', sa.String(), nullable=False),
        sa.Column('valor_nfse', sa.Numeric(14, 2), nullable=False),
        sa.Column('impostos', sa.Numeric(14, 2), nullable=False),
        sa.Column('processo_recebimento', sa.String(), nullable=False),
        sa.Column('data_previsao_recebimento', sa.Date(), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('data_recebimento', sa.Date(), nullable=True),
        sa.Column('conta_bancaria_id', sa.Integer(), nullable=True),
        sa.Column('conta_plano_contas', sa.String(), nullable=True),
        sa.Column('conta_centro_custo', sa.String(), nullable=True),
        sa.Column('lancamento_extrato_id', sa.Integer(), nullable=True),
        sa.Column(
            'data_criacao',
            sa.DateTime(),
            server_default=sa.text("timezone('America/Sao_Paulo', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['conta_bancaria_id'],
            [f'{SCHEMA_NAME}.contas_bancarias_recebimento.id'],
        ),
        sa.ForeignKeyConstraint(
            ['lancamento_extrato_id'],
            [f'{SCHEMA_NAME}.lancamentos_extrato_bancario.id'],
        ),
        sa.ForeignKeyConstraint(
            ['usuario_id'],
            [f'{SCHEMA_NAME}.usuarios_api.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'nfse_row_hash',
            name='uq_conciliacoes_faturamento_nfse',
        ),
        schema=SCHEMA_NAME,
    )
    op.create_table(
        'conciliacoes_faturamento_remessas',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conciliacao_id', sa.Integer(), nullable=False),
        sa.Column('cd_remessa', sa.Integer(), nullable=False),
        sa.Column('convenio', sa.String(), nullable=False),
        sa.Column('cnpj_convenio', sa.String(), nullable=False),
        sa.Column('valor_total', sa.Numeric(14, 2), nullable=False),
        sa.Column(
            'sn_glosado',
            sa.String(),
            server_default=sa.text("'not'"),
            nullable=False,
        ),
        sa.Column(
            'valor_glosado',
            sa.Numeric(14, 2),
            server_default=sa.text('0'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['conciliacao_id'],
            [f'{SCHEMA_NAME}.conciliacoes_faturamento.id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'cd_remessa',
            name='uq_conciliacoes_faturamento_remessas_cd_remessa',
        ),
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_table(
        'conciliacoes_faturamento_remessas',
        schema=SCHEMA_NAME,
    )
    op.drop_table('conciliacoes_faturamento', schema=SCHEMA_NAME)
    op.drop_index(
        'ix_lancamentos_extrato_conta_data',
        table_name='lancamentos_extrato_bancario',
        schema=SCHEMA_NAME,
    )
    op.drop_table('lancamentos_extrato_bancario', schema=SCHEMA_NAME)
    op.drop_table('contas_bancarias_recebimento', schema=SCHEMA_NAME)
