"""cria empresas emissoras e vincula CNPJ às emissões

Revision ID: 20260726_033
Revises: 20260724_032
Create Date: 2026-07-26 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app_prontocardio.settings import Settings

revision: str = '20260726_033'
down_revision: str | Sequence[str] | None = '20260724_032'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_NAME = Settings().POSTGRES_SCHEMA


def upgrade() -> None:
    op.create_table(
        'empresas_emissoras',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('cnpj', sa.String(length=14), nullable=False),
        sa.Column('razao_social', sa.String(length=200), nullable=False),
        sa.Column('usuario_criacao_id', sa.Integer(), nullable=True),
        sa.Column('usuario_atualizacao_id', sa.Integer(), nullable=True),
        sa.Column(
            'ativo',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('true'),
        ),
        sa.Column(
            'data_criacao',
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            'data_atualizacao',
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            'length(cnpj) = 14',
            name='ck_empresas_emissoras_cnpj',
        ),
        sa.ForeignKeyConstraint(
            ['usuario_criacao_id'],
            [f'{SCHEMA_NAME}.usuarios_api.id'],
        ),
        sa.ForeignKeyConstraint(
            ['usuario_atualizacao_id'],
            [f'{SCHEMA_NAME}.usuarios_api.id'],
        ),
        sa.UniqueConstraint('cnpj', name='uq_empresas_emissoras_cnpj'),
        schema=SCHEMA_NAME,
    )
    op.create_table(
        'empresas_emissoras_eventos',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('empresa_emissora_id', sa.Integer(), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=True),
        sa.Column('tipo_acao', sa.String(length=30), nullable=False),
        sa.Column('dados_anteriores', sa.JSON(), nullable=True),
        sa.Column('dados_novos', sa.JSON(), nullable=True),
        sa.Column(
            'data_criacao',
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ['empresa_emissora_id'],
            [f'{SCHEMA_NAME}.empresas_emissoras.id'],
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['usuario_id'],
            [f'{SCHEMA_NAME}.usuarios_api.id'],
        ),
        schema=SCHEMA_NAME,
    )
    op.create_index(
        'ix_empresas_emissoras_eventos_empresa',
        'empresas_emissoras_eventos',
        ['empresa_emissora_id', 'data_criacao'],
        schema=SCHEMA_NAME,
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {SCHEMA_NAME}.empresas_emissoras (
                cnpj,
                razao_social,
                usuario_criacao_id,
                usuario_atualizacao_id,
                ativo
            )
            VALUES
                (
                    '05613278000158',
                    'PRONTOCARDIO PRONTOATENDIMENTO CARDIOLOGICO LTDA',
                    NULL,
                    NULL,
                    true
                ),
                (
                    '08711085000128',
                    'PRONTOCARDIO SERVICOS MEDICOS HOSPITALARES LTDA',
                    NULL,
                    NULL,
                    true
                )
            ON CONFLICT (cnpj) DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {SCHEMA_NAME}.empresas_emissoras_eventos (
                empresa_emissora_id,
                usuario_id,
                tipo_acao,
                dados_novos
            )
            SELECT
                empresa.id,
                NULL,
                'CARGA_INICIAL',
                json_build_object(
                    'cnpj', empresa.cnpj,
                    'razao_social', empresa.razao_social,
                    'ativo', empresa.ativo
                )
            FROM {SCHEMA_NAME}.empresas_emissoras AS empresa
            WHERE empresa.cnpj IN (
                '05613278000158',
                '08711085000128'
            )
            """
        )
    )
    for table_name in ('solicitacao_nota', 'emissao_nfse'):
        op.add_column(
            table_name,
            sa.Column('empresa_emissora_id', sa.Integer(), nullable=True),
            schema=SCHEMA_NAME,
        )
        op.add_column(
            table_name,
            sa.Column('cnpj_emissor', sa.String(length=14), nullable=True),
            schema=SCHEMA_NAME,
        )
        op.add_column(
            table_name,
            sa.Column(
                'razao_social_emissor',
                sa.String(length=200),
                nullable=True,
            ),
            schema=SCHEMA_NAME,
        )
        op.create_foreign_key(
            f'fk_{table_name}_empresa_emissora',
            table_name,
            'empresas_emissoras',
            ['empresa_emissora_id'],
            ['id'],
            source_schema=SCHEMA_NAME,
            referent_schema=SCHEMA_NAME,
            ondelete='RESTRICT',
        )
        op.create_index(
            f'ix_{table_name}_cnpj_emissor',
            table_name,
            ['cnpj_emissor'],
            schema=SCHEMA_NAME,
        )


def downgrade() -> None:
    for table_name in ('emissao_nfse', 'solicitacao_nota'):
        op.drop_index(
            f'ix_{table_name}_cnpj_emissor',
            table_name=table_name,
            schema=SCHEMA_NAME,
        )
        op.drop_constraint(
            f'fk_{table_name}_empresa_emissora',
            table_name,
            schema=SCHEMA_NAME,
            type_='foreignkey',
        )
        op.drop_column(
            table_name,
            'razao_social_emissor',
            schema=SCHEMA_NAME,
        )
        op.drop_column(table_name, 'cnpj_emissor', schema=SCHEMA_NAME)
        op.drop_column(
            table_name,
            'empresa_emissora_id',
            schema=SCHEMA_NAME,
        )
    op.drop_index(
        'ix_empresas_emissoras_eventos_empresa',
        table_name='empresas_emissoras_eventos',
        schema=SCHEMA_NAME,
    )
    op.drop_table('empresas_emissoras_eventos', schema=SCHEMA_NAME)
    op.drop_table('empresas_emissoras', schema=SCHEMA_NAME)
