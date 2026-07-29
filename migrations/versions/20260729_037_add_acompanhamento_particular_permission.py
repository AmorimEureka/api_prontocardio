"""adiciona permissão do acompanhamento de atendimentos particulares

Revision ID: 20260729_037
Revises: 20260727_036
Create Date: 2026-07-29 16:30:00.000000
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app_prontocardio.permissions import TELAS_PADRAO_JSON, TELAS_SISTEMA
from app_prontocardio.settings import Settings

revision: str = '20260729_037'
down_revision: str | Sequence[str] | None = '20260727_036'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_NAME = Settings().POSTGRES_SCHEMA
PERMISSAO = 'acompanhamento_particular'
TELAS_ANTERIORES_JSON = json.dumps(
    [tela for tela in TELAS_SISTEMA if tela != PERMISSAO]
)


def upgrade() -> None:
    op.alter_column(
        'usuarios_api',
        'telas_permitidas',
        server_default=sa.text(f"'{TELAS_PADRAO_JSON}'::json"),
        schema=SCHEMA_NAME,
    )
    op.execute(
        sa.text(
            f'''
            UPDATE "{SCHEMA_NAME}"."usuarios_api"
               SET telas_permitidas = (
                   telas_permitidas::jsonb || '["{PERMISSAO}"]'::jsonb
               )::json
             WHERE NOT (
                 telas_permitidas::jsonb ? '{PERMISSAO}'
             )
               AND (
                   telas_permitidas::jsonb ? 'follow_up_solicitacoes'
                   OR telas_permitidas::jsonb ? 'emissao_nfse'
               )
            '''
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            f'''
            UPDATE "{SCHEMA_NAME}"."usuarios_api"
               SET telas_permitidas = COALESCE(
                   (
                       SELECT json_agg(valor)
                         FROM json_array_elements_text(
                             telas_permitidas
                         ) AS item(valor)
                        WHERE valor <> '{PERMISSAO}'
                   ),
                   '[]'::json
               )
            '''
        )
    )
    op.alter_column(
        'usuarios_api',
        'telas_permitidas',
        server_default=sa.text(f"'{TELAS_ANTERIORES_JSON}'::json"),
        schema=SCHEMA_NAME,
    )
