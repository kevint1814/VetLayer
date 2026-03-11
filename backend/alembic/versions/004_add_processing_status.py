"""Add processing_status column to candidates table.

Revision ID: 004
Create Date: 2026-03-11
"""
from alembic import op
import sqlalchemy as sa

revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('candidates', sa.Column('processing_status', sa.String(20), nullable=True, server_default='ready'))
    # Set existing candidates to 'ready' since they're already parsed
    op.execute("UPDATE candidates SET processing_status = 'ready' WHERE processing_status IS NULL")


def downgrade() -> None:
    op.drop_column('candidates', 'processing_status')
