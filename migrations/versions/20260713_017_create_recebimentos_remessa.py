"""create remittance receipt installments

Revision ID: 20260713_017
Revises: 20260712_016
Create Date: 2026-07-13 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260713_017'
down_revision: Union[str, Sequence[str], None] = '20260712_016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'


def upgrade() -> None:
    op.create_table(
        'remessas_financeiras',
        sa.Column(
            'cd_remessa',
            sa.Integer(),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column('convenio', sa.String(), nullable=False),
        sa.Column('cnpj_convenio', sa.String(), nullable=False),
        sa.Column('valor_total', sa.Numeric(14, 2), nullable=False),
        sa.Column(
            'recebimento_integral',
            sa.Boolean(),
            server_default=sa.text('false'),
            nullable=False,
        ),
        sa.Column(
            'data_registro',
            sa.DateTime(),
            server_default=sa.text(
                "timezone('America/Sao_Paulo', now())"
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            'valor_total >= 0',
            name='ck_remessas_financeiras_valor_total',
        ),
        sa.PrimaryKeyConstraint('cd_remessa'),
        schema=SCHEMA_NAME,
    )
    op.create_table(
        'recebimentos_remessas',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cd_remessa', sa.Integer(), nullable=False),
        sa.Column('conciliacao_id', sa.Integer(), nullable=False),
        sa.Column('numero_nfse', sa.String(), nullable=False),
        sa.Column('data_recebimento', sa.Date(), nullable=False),
        sa.Column('valor_recebido', sa.Numeric(14, 2), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('conta_bancaria_id', sa.Integer(), nullable=False),
        sa.Column('conta_plano_contas', sa.String(), nullable=True),
        sa.Column('conta_centro_custo', sa.String(), nullable=True),
        sa.Column('lancamento_extrato_id', sa.Integer(), nullable=True),
        sa.Column(
            'recebimento_integral',
            sa.Boolean(),
            server_default=sa.text('false'),
            nullable=False,
        ),
        sa.Column(
            'data_registro',
            sa.DateTime(),
            server_default=sa.text(
                "timezone('America/Sao_Paulo', now())"
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            'valor_recebido > 0',
            name='ck_recebimentos_remessas_valor_positivo',
        ),
        sa.ForeignKeyConstraint(
            ['cd_remessa'],
            [f'{SCHEMA_NAME}.remessas_financeiras.cd_remessa'],
        ),
        sa.ForeignKeyConstraint(
            ['conciliacao_id', 'cd_remessa'],
            [
                f'{SCHEMA_NAME}.'
                'conciliacoes_faturamento_remessas.conciliacao_id',
                f'{SCHEMA_NAME}.'
                'conciliacoes_faturamento_remessas.cd_remessa',
            ],
            name='fk_recebimento_conciliacao_remessa',
            ondelete='CASCADE',
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
            'conciliacao_id',
            'cd_remessa',
            name='uq_recebimento_conciliacao_remessa',
        ),
        schema=SCHEMA_NAME,
    )
    op.create_index(
        'ix_recebimentos_remessas_codigo_data',
        'recebimentos_remessas',
        ['cd_remessa', 'data_recebimento'],
        schema=SCHEMA_NAME,
    )
    op.create_index(
        'ix_recebimentos_remessas_nfse',
        'recebimentos_remessas',
        ['numero_nfse'],
        schema=SCHEMA_NAME,
    )
    op.create_index(
        'ix_recebimentos_remessas_lancamento',
        'recebimentos_remessas',
        ['lancamento_extrato_id'],
        schema=SCHEMA_NAME,
    )

    op.execute(
        sa.text(
            f"""
            INSERT INTO {SCHEMA_NAME}.remessas_financeiras (
                cd_remessa,
                convenio,
                cnpj_convenio,
                valor_total,
                recebimento_integral,
                data_registro
            )
            SELECT
                remessa.cd_remessa,
                MAX(remessa.convenio),
                MAX(remessa.cnpj_convenio),
                MAX(remessa.valor_total),
                false,
                MIN(conciliacao.data_criacao)
            FROM {SCHEMA_NAME}.conciliacoes_faturamento_remessas remessa
            JOIN {SCHEMA_NAME}.conciliacoes_faturamento conciliacao
              ON conciliacao.id = remessa.conciliacao_id
            GROUP BY remessa.cd_remessa
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {SCHEMA_NAME}.recebimentos_remessas (
                cd_remessa,
                conciliacao_id,
                numero_nfse,
                data_recebimento,
                valor_recebido,
                usuario_id,
                conta_bancaria_id,
                conta_plano_contas,
                conta_centro_custo,
                lancamento_extrato_id,
                recebimento_integral,
                data_registro
            )
            SELECT
                remessa.cd_remessa,
                conciliacao.id,
                conciliacao.numero_nfse,
                conciliacao.data_recebimento,
                GREATEST(
                    remessa.valor_total - remessa.valor_glosado,
                    0
                ),
                conciliacao.usuario_id,
                conciliacao.conta_bancaria_id,
                conciliacao.conta_plano_contas,
                conciliacao.conta_centro_custo,
                conciliacao.lancamento_extrato_id,
                false,
                conciliacao.data_criacao
            FROM {SCHEMA_NAME}.conciliacoes_faturamento_remessas remessa
            JOIN {SCHEMA_NAME}.conciliacoes_faturamento conciliacao
              ON conciliacao.id = remessa.conciliacao_id
            WHERE conciliacao.data_recebimento IS NOT NULL
              AND conciliacao.conta_bancaria_id IS NOT NULL
              AND remessa.valor_total - remessa.valor_glosado > 0
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            WITH totais AS (
                SELECT cd_remessa, SUM(valor_recebido) AS valor_recebido
                FROM {SCHEMA_NAME}.recebimentos_remessas
                GROUP BY cd_remessa
            )
            UPDATE {SCHEMA_NAME}.remessas_financeiras remessa
               SET recebimento_integral = (
                   totais.valor_recebido = remessa.valor_total
               )
              FROM totais
             WHERE totais.cd_remessa = remessa.cd_remessa
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            WITH acumulados AS (
                SELECT
                    recebimento.id,
                    recebimento.cd_remessa,
                    SUM(recebimento.valor_recebido) OVER (
                        PARTITION BY recebimento.cd_remessa
                        ORDER BY
                            recebimento.data_recebimento,
                            recebimento.id
                    ) AS valor_acumulado
                FROM {SCHEMA_NAME}.recebimentos_remessas recebimento
            )
            UPDATE {SCHEMA_NAME}.recebimentos_remessas recebimento
               SET recebimento_integral = true
              FROM acumulados, {SCHEMA_NAME}.remessas_financeiras remessa
             WHERE acumulados.id = recebimento.id
               AND remessa.cd_remessa = acumulados.cd_remessa
               AND acumulados.valor_acumulado = remessa.valor_total
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        'ix_recebimentos_remessas_lancamento',
        table_name='recebimentos_remessas',
        schema=SCHEMA_NAME,
    )
    op.drop_index(
        'ix_recebimentos_remessas_nfse',
        table_name='recebimentos_remessas',
        schema=SCHEMA_NAME,
    )
    op.drop_index(
        'ix_recebimentos_remessas_codigo_data',
        table_name='recebimentos_remessas',
        schema=SCHEMA_NAME,
    )
    op.drop_table('recebimentos_remessas', schema=SCHEMA_NAME)
    op.drop_table('remessas_financeiras', schema=SCHEMA_NAME)
