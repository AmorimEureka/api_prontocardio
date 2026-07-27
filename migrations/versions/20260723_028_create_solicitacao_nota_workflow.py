"""create solicitacao nota workflow

Revision ID: 20260723_028
Revises: 20260723_027
Create Date: 2026-07-23 18:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260723_028'
down_revision: Union[str, Sequence[str], None] = '20260723_027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
LOCAL_NOW = sa.text("timezone('America/Sao_Paulo', now())")


def upgrade() -> None:
    op.create_table(
        'solicitacao_nota_workflow',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('solicitacao_nota_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('validacao', sa.String(length=20), nullable=True),
        sa.Column('motivo_recusa', sa.String(length=500), nullable=True),
        sa.Column('validado_por_id', sa.Integer(), nullable=True),
        sa.Column('validado_em', sa.DateTime(), nullable=True),
        sa.Column(
            'data_criacao',
            sa.DateTime(),
            server_default=LOCAL_NOW,
            nullable=False,
        ),
        sa.Column(
            'data_atualizacao',
            sa.DateTime(),
            server_default=LOCAL_NOW,
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ("
            "'PENDENTE_VALIDACAO', 'RECUSADA', 'VALIDADA', "
            "'EMISSAO_SOLICITADA', 'EMITIDA', 'ERRO_EMISSAO'"
            ')',
            name='ck_solicitacao_nota_workflow_status',
        ),
        sa.CheckConstraint(
            "validacao IS NULL OR validacao IN ('VALIDADA', 'RECUSADA')",
            name='ck_solicitacao_nota_workflow_validacao',
        ),
        sa.ForeignKeyConstraint(
            ['solicitacao_nota_id'],
            [f'{SCHEMA_NAME}.solicitacao_nota.id'],
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['validado_por_id'],
            [f'{SCHEMA_NAME}.usuarios_api.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('solicitacao_nota_id'),
        schema=SCHEMA_NAME,
    )
    op.create_index(
        'ix_solicitacao_nota_workflow_status',
        'solicitacao_nota_workflow',
        ['status', 'solicitacao_nota_id'],
        schema=SCHEMA_NAME,
    )

    op.create_table(
        'solicitacao_nota_evento',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('solicitacao_nota_id', sa.Integer(), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('tipo_acao', sa.String(length=40), nullable=False),
        sa.Column('observacao', sa.String(length=500), nullable=True),
        sa.Column(
            'data_criacao',
            sa.DateTime(),
            server_default=LOCAL_NOW,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['solicitacao_nota_id'],
            [f'{SCHEMA_NAME}.solicitacao_nota.id'],
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
        'ix_solicitacao_nota_evento_solicitacao',
        'solicitacao_nota_evento',
        ['solicitacao_nota_id', 'data_criacao'],
        schema=SCHEMA_NAME,
    )

    op.create_table(
        'lote_emissao_nfse',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tipo', sa.String(length=20), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('dag_run_id', sa.String(length=250), nullable=True),
        sa.Column('airflow_disparado_em', sa.DateTime(), nullable=True),
        sa.Column('erro_disparo', sa.String(length=1000), nullable=True),
        sa.Column(
            'data_criacao',
            sa.DateTime(),
            server_default=LOCAL_NOW,
            nullable=False,
        ),
        sa.CheckConstraint(
            "tipo IN ('INDIVIDUAL', 'LOTE')",
            name='ck_lote_emissao_nfse_tipo',
        ),
        sa.CheckConstraint(
            "status IN ('PENDENTE', 'PROCESSANDO', 'EMITIDA', 'ERRO')",
            name='ck_lote_emissao_nfse_status',
        ),
        sa.ForeignKeyConstraint(
            ['usuario_id'],
            [f'{SCHEMA_NAME}.usuarios_api.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
        schema=SCHEMA_NAME,
    )

    op.create_table(
        'emissao_nfse',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('solicitacao_nota_id', sa.Integer(), nullable=False),
        sa.Column('lote_id', sa.Integer(), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('numero_nfse', sa.String(length=100), nullable=True),
        sa.Column('protocolo', sa.String(length=200), nullable=True),
        sa.Column('erro', sa.String(length=1000), nullable=True),
        sa.Column(
            'data_criacao',
            sa.DateTime(),
            server_default=LOCAL_NOW,
            nullable=False,
        ),
        sa.Column(
            'data_atualizacao',
            sa.DateTime(),
            server_default=LOCAL_NOW,
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDENTE', 'PROCESSANDO', 'EMITIDA', 'ERRO')",
            name='ck_emissao_nfse_status',
        ),
        sa.ForeignKeyConstraint(
            ['lote_id'],
            [f'{SCHEMA_NAME}.lote_emissao_nfse.id'],
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['solicitacao_nota_id'],
            [f'{SCHEMA_NAME}.solicitacao_nota.id'],
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['usuario_id'],
            [f'{SCHEMA_NAME}.usuarios_api.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
        schema=SCHEMA_NAME,
    )
    op.create_index(
        'ix_emissao_nfse_status',
        'emissao_nfse',
        ['status', 'solicitacao_nota_id'],
        schema=SCHEMA_NAME,
    )

    op.execute(
        sa.text(
            f"""
            INSERT INTO {SCHEMA_NAME}.solicitacao_nota_workflow (
                solicitacao_nota_id,
                status,
                data_criacao,
                data_atualizacao
            )
            SELECT id, 'PENDENTE_VALIDACAO', data_criacao, data_criacao
              FROM {SCHEMA_NAME}.solicitacao_nota
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {SCHEMA_NAME}.solicitacao_nota_evento (
                solicitacao_nota_id,
                usuario_id,
                tipo_acao,
                observacao,
                data_criacao
            )
            SELECT
                id,
                usuario_id,
                'CRIACAO',
                'Solicitação incorporada ao workflow.',
                data_criacao
              FROM {SCHEMA_NAME}.solicitacao_nota
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        'ix_emissao_nfse_status',
        table_name='emissao_nfse',
        schema=SCHEMA_NAME,
    )
    op.drop_table('emissao_nfse', schema=SCHEMA_NAME)
    op.drop_table('lote_emissao_nfse', schema=SCHEMA_NAME)
    op.drop_index(
        'ix_solicitacao_nota_evento_solicitacao',
        table_name='solicitacao_nota_evento',
        schema=SCHEMA_NAME,
    )
    op.drop_table('solicitacao_nota_evento', schema=SCHEMA_NAME)
    op.drop_index(
        'ix_solicitacao_nota_workflow_status',
        table_name='solicitacao_nota_workflow',
        schema=SCHEMA_NAME,
    )
    op.drop_table('solicitacao_nota_workflow', schema=SCHEMA_NAME)
