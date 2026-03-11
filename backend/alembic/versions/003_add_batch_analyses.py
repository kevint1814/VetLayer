"""Add batch_analyses table for persistent batch history.

Revision ID: 003
Create Date: 2026-03-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '003'
down_revision = None  # adjust if you have prior migrations
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'batch_analyses',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('batch_id', sa.String(20), nullable=False, unique=True, index=True),
        sa.Column('candidate_ids', JSONB, nullable=False),
        sa.Column('job_ids', JSONB, nullable=False),
        sa.Column('status', sa.String(30), server_default='processing'),
        sa.Column('total', sa.Integer, server_default='0'),
        sa.Column('completed', sa.Integer, server_default='0'),
        sa.Column('failed', sa.Integer, server_default='0'),
        sa.Column('cached', sa.Integer, server_default='0'),
        sa.Column('elapsed_ms', sa.Integer, nullable=True),
        sa.Column('results', JSONB, nullable=True),
        sa.Column('job_titles', JSONB, nullable=True),
        sa.Column('candidate_count', sa.Integer, server_default='0'),
        sa.Column('avg_score', sa.Float, server_default='0.0'),
        sa.Column('top_recommendation', sa.String(50), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('batch_analyses')
