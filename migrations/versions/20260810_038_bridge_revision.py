"""bridge deployed demonstrativo IPM revision identifier

Revision ID: 20260810_038
Revises: 20260809_038
Create Date: 2026-08-10 12:00:00.000000

This no-op revision preserves the identifier already stamped in deployed
databases while keeping fresh installations on the complete migration chain.
"""

from typing import Sequence, Union

revision: str = '20260810_038'
down_revision: Union[str, Sequence[str], None] = '20260809_038'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
