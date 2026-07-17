"""audit and inactivate financial reconciliations

Revision ID: 20260717_023
Revises: 20260716_022
Create Date: 2026-07-17 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260717_023'
down_revision: Union[str, Sequence[str], None] = '20260716_022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'


def upgrade() -> None:
    op.add_column(
        'conciliacoes_faturamento',
        sa.Column(
            'ativo',
            sa.Boolean(),
            server_default=sa.text('true'),
            nullable=False,
        ),
        schema=SCHEMA_NAME,
    )
    op.add_column(
        'conciliacoes_faturamento',
        sa.Column('usuario_atualizacao_id', sa.Integer(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.add_column(
        'conciliacoes_faturamento',
        sa.Column('data_atualizacao', sa.DateTime(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.add_column(
        'conciliacoes_faturamento',
        sa.Column('usuario_inativacao_id', sa.Integer(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.add_column(
        'conciliacoes_faturamento',
        sa.Column('data_inativacao', sa.DateTime(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.create_foreign_key(
        'fk_conciliacao_usuario_atualizacao',
        'conciliacoes_faturamento',
        'usuarios_api',
        ['usuario_atualizacao_id'],
        ['id'],
        source_schema=SCHEMA_NAME,
        referent_schema=SCHEMA_NAME,
    )
    op.create_foreign_key(
        'fk_conciliacao_usuario_inativacao',
        'conciliacoes_faturamento',
        'usuarios_api',
        ['usuario_inativacao_id'],
        ['id'],
        source_schema=SCHEMA_NAME,
        referent_schema=SCHEMA_NAME,
    )
    op.create_index(
        'ix_conciliacoes_faturamento_ativo',
        'conciliacoes_faturamento',
        ['ativo'],
        schema=SCHEMA_NAME,
    )

    op.add_column(
        'processos_conciliacao_remessa',
        sa.Column('usuario_atualizacao_id', sa.Integer(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.add_column(
        'processos_conciliacao_remessa',
        sa.Column('data_atualizacao', sa.DateTime(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.create_foreign_key(
        'fk_processo_conciliacao_usuario_atualizacao',
        'processos_conciliacao_remessa',
        'usuarios_api',
        ['usuario_atualizacao_id'],
        ['id'],
        source_schema=SCHEMA_NAME,
        referent_schema=SCHEMA_NAME,
    )

    op.create_table(
        'auditorias_conciliacao_faturamento',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conciliacao_id', sa.Integer(), nullable=False),
        sa.Column('acao', sa.String(length=40), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('dados_anteriores', sa.JSON(), nullable=True),
        sa.Column('dados_novos', sa.JSON(), nullable=True),
        sa.Column(
            'data_operacao',
            sa.DateTime(),
            server_default=sa.text(
                "timezone('America/Sao_Paulo', now())"
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['conciliacao_id'],
            [f'{SCHEMA_NAME}.conciliacoes_faturamento.id'],
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['usuario_id'],
            [f'{SCHEMA_NAME}.usuarios_api.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
        schema=SCHEMA_NAME,
    )
    op.create_index(
        'ix_auditoria_conciliacao_data',
        'auditorias_conciliacao_faturamento',
        ['conciliacao_id', 'data_operacao'],
        schema=SCHEMA_NAME,
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {SCHEMA_NAME}.auditorias_conciliacao_faturamento (
                conciliacao_id,
                acao,
                usuario_id,
                dados_novos,
                data_operacao
            )
            SELECT
                id,
                'criacao_migrada',
                usuario_id,
                json_build_object(
                    'numero_nfse', numero_nfse,
                    'processo_recebimento', processo_recebimento,
                    'data_previsao_recebimento',
                    data_previsao_recebimento
                ),
                data_criacao
            FROM {SCHEMA_NAME}.conciliacoes_faturamento
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        'ix_auditoria_conciliacao_data',
        table_name='auditorias_conciliacao_faturamento',
        schema=SCHEMA_NAME,
    )
    op.drop_table(
        'auditorias_conciliacao_faturamento',
        schema=SCHEMA_NAME,
    )
    op.drop_constraint(
        'fk_processo_conciliacao_usuario_atualizacao',
        'processos_conciliacao_remessa',
        schema=SCHEMA_NAME,
        type_='foreignkey',
    )
    op.drop_column(
        'processos_conciliacao_remessa',
        'data_atualizacao',
        schema=SCHEMA_NAME,
    )
    op.drop_column(
        'processos_conciliacao_remessa',
        'usuario_atualizacao_id',
        schema=SCHEMA_NAME,
    )
    op.drop_index(
        'ix_conciliacoes_faturamento_ativo',
        table_name='conciliacoes_faturamento',
        schema=SCHEMA_NAME,
    )
    op.drop_constraint(
        'fk_conciliacao_usuario_inativacao',
        'conciliacoes_faturamento',
        schema=SCHEMA_NAME,
        type_='foreignkey',
    )
    op.drop_constraint(
        'fk_conciliacao_usuario_atualizacao',
        'conciliacoes_faturamento',
        schema=SCHEMA_NAME,
        type_='foreignkey',
    )
    op.drop_column(
        'conciliacoes_faturamento',
        'data_inativacao',
        schema=SCHEMA_NAME,
    )
    op.drop_column(
        'conciliacoes_faturamento',
        'usuario_inativacao_id',
        schema=SCHEMA_NAME,
    )
    op.drop_column(
        'conciliacoes_faturamento',
        'data_atualizacao',
        schema=SCHEMA_NAME,
    )
    op.drop_column(
        'conciliacoes_faturamento',
        'usuario_atualizacao_id',
        schema=SCHEMA_NAME,
    )
    op.drop_column(
        'conciliacoes_faturamento',
        'ativo',
        schema=SCHEMA_NAME,
    )
