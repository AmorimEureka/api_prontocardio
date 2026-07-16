"""center reconciliation on remittance and allocate nfse balances

Revision ID: 20260716_022
Revises: 20260716_021
Create Date: 2026-07-16 15:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260716_022'
down_revision: Union[str, Sequence[str], None] = '20260716_021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'


def upgrade() -> None:
    op.create_table(
        'processos_conciliacao_remessa',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cd_remessa', sa.Integer(), nullable=False),
        sa.Column('processo_recebimento', sa.String(), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column(
            'data_criacao',
            sa.DateTime(),
            server_default=sa.text(
                "timezone('America/Sao_Paulo', now())"
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['cd_remessa'],
            [f'{SCHEMA_NAME}.remessas_financeiras.cd_remessa'],
        ),
        sa.ForeignKeyConstraint(
            ['usuario_id'],
            [f'{SCHEMA_NAME}.usuarios_api.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'cd_remessa',
            name='uq_processos_conciliacao_remessa_codigo',
        ),
        schema=SCHEMA_NAME,
    )
    op.add_column(
        'remessas_financeiras',
        sa.Column('data_competencia', sa.Date(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.add_column(
        'conciliacoes_faturamento_remessas',
        sa.Column('processo_remessa_id', sa.Integer(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.add_column(
        'conciliacoes_faturamento_remessas',
        sa.Column(
            'valor_alocado_nfse',
            sa.Numeric(14, 2),
            server_default=sa.text('0'),
            nullable=False,
        ),
        schema=SCHEMA_NAME,
    )
    op.create_foreign_key(
        'fk_conciliacoes_remessas_processo',
        'conciliacoes_faturamento_remessas',
        'processos_conciliacao_remessa',
        ['processo_remessa_id'],
        ['id'],
        source_schema=SCHEMA_NAME,
        referent_schema=SCHEMA_NAME,
        ondelete='CASCADE',
    )
    op.create_index(
        'ix_conciliacoes_remessas_processo',
        'conciliacoes_faturamento_remessas',
        ['processo_remessa_id'],
        schema=SCHEMA_NAME,
    )

    op.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA_NAME}.conciliacoes_faturamento_remessas
               SET valor_alocado_nfse = GREATEST(
                   valor_total - valor_glosado,
                   0
               )
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {SCHEMA_NAME}.processos_conciliacao_remessa (
                cd_remessa,
                processo_recebimento,
                usuario_id,
                data_criacao
            )
            SELECT DISTINCT ON (vinculo.cd_remessa)
                vinculo.cd_remessa,
                conciliacao.processo_recebimento,
                conciliacao.usuario_id,
                conciliacao.data_criacao
            FROM {SCHEMA_NAME}.conciliacoes_faturamento_remessas vinculo
            JOIN {SCHEMA_NAME}.conciliacoes_faturamento conciliacao
              ON conciliacao.id = vinculo.conciliacao_id
            ORDER BY
                vinculo.cd_remessa,
                conciliacao.data_criacao,
                conciliacao.id
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA_NAME}.conciliacoes_faturamento_remessas vinculo
               SET processo_remessa_id = processo.id
              FROM {SCHEMA_NAME}.processos_conciliacao_remessa processo
             WHERE processo.cd_remessa = vinculo.cd_remessa
            """
        )
    )

    op.drop_constraint(
        'uq_conciliacoes_faturamento_nfse',
        'conciliacoes_faturamento',
        schema=SCHEMA_NAME,
        type_='unique',
    )
    op.drop_constraint(
        'uq_conciliacoes_faturamento_numero_nfse',
        'conciliacoes_faturamento',
        schema=SCHEMA_NAME,
        type_='unique',
    )
    op.create_index(
        'ix_conciliacoes_faturamento_nfse_saldo',
        'conciliacoes_faturamento',
        ['numero_nfse', 'cnpj_convenio'],
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_index(
        'ix_conciliacoes_faturamento_nfse_saldo',
        table_name='conciliacoes_faturamento',
        schema=SCHEMA_NAME,
    )
    op.create_unique_constraint(
        'uq_conciliacoes_faturamento_numero_nfse',
        'conciliacoes_faturamento',
        ['numero_nfse'],
        schema=SCHEMA_NAME,
    )
    op.create_unique_constraint(
        'uq_conciliacoes_faturamento_nfse',
        'conciliacoes_faturamento',
        ['nfse_row_hash'],
        schema=SCHEMA_NAME,
    )
    op.drop_index(
        'ix_conciliacoes_remessas_processo',
        table_name='conciliacoes_faturamento_remessas',
        schema=SCHEMA_NAME,
    )
    op.drop_constraint(
        'fk_conciliacoes_remessas_processo',
        'conciliacoes_faturamento_remessas',
        schema=SCHEMA_NAME,
        type_='foreignkey',
    )
    op.drop_column(
        'conciliacoes_faturamento_remessas',
        'valor_alocado_nfse',
        schema=SCHEMA_NAME,
    )
    op.drop_column(
        'conciliacoes_faturamento_remessas',
        'processo_remessa_id',
        schema=SCHEMA_NAME,
    )
    op.drop_column(
        'remessas_financeiras',
        'data_competencia',
        schema=SCHEMA_NAME,
    )
    op.drop_table('processos_conciliacao_remessa', schema=SCHEMA_NAME)
