"""Add multi-tenancy support with Company model and company_id to all data tables.

Revision ID: 005
Create Date: 2026-03-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
import uuid as uuid_mod

revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None

# Fixed UUID for "Live Connections" company (deterministic for all environments)
LIVE_CONNECTIONS_UUID = '00000000-0000-0000-0000-000000000001'


def upgrade() -> None:
    # ── 1. Create companies table ────────────────────────────────────────
    op.create_table(
        'companies',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text(f"'{LIVE_CONNECTIONS_UUID}'::uuid")),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('slug', sa.String(100), nullable=False, unique=True, index=True),
        sa.Column('is_active', sa.Boolean, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )

    # ── 2. Insert Live Connections company ───────────────────────────────
    op.execute(f"""
        INSERT INTO companies (id, name, slug, is_active, created_at, updated_at)
        VALUES ('{LIVE_CONNECTIONS_UUID}'::uuid, 'Live Connections', 'live-connections', true, now(), now())
        ON CONFLICT DO NOTHING
    """)

    # ── 3. Add company_id to users table (nullable initially) ───────────
    op.add_column('users', sa.Column('company_id', UUID(as_uuid=True), nullable=True, index=True))
    op.create_foreign_key('fk_users_company_id', 'users', 'companies', ['company_id'], ['id'])

    # ── 4. Add company_id to candidates table ────────────────────────────
    op.add_column('candidates', sa.Column('company_id', UUID(as_uuid=True), nullable=True, index=True))
    op.execute(f"""
        UPDATE candidates SET company_id = '{LIVE_CONNECTIONS_UUID}'::uuid
        WHERE company_id IS NULL
    """)
    op.alter_column('candidates', 'company_id', nullable=False)
    op.create_foreign_key('fk_candidates_company_id', 'candidates', 'companies', ['company_id'], ['id'])

    # ── 5. Add company_id to jobs table ──────────────────────────────────
    op.add_column('jobs', sa.Column('company_id', UUID(as_uuid=True), nullable=True, index=True))
    op.execute(f"""
        UPDATE jobs SET company_id = '{LIVE_CONNECTIONS_UUID}'::uuid
        WHERE company_id IS NULL
    """)
    op.alter_column('jobs', 'company_id', nullable=False)
    op.create_foreign_key('fk_jobs_company_id', 'jobs', 'companies', ['company_id'], ['id'])

    # ── 6. Add company_id to analysis_results table ──────────────────────
    op.add_column('analysis_results', sa.Column('company_id', UUID(as_uuid=True), nullable=True, index=True))
    op.execute(f"""
        UPDATE analysis_results SET company_id = '{LIVE_CONNECTIONS_UUID}'::uuid
        WHERE company_id IS NULL
    """)
    op.alter_column('analysis_results', 'company_id', nullable=False)
    op.create_foreign_key('fk_analysis_results_company_id', 'analysis_results', 'companies', ['company_id'], ['id'])

    # ── 7. Add company_id to batch_analyses table ────────────────────────
    op.add_column('batch_analyses', sa.Column('company_id', UUID(as_uuid=True), nullable=True, index=True))
    op.execute(f"""
        UPDATE batch_analyses SET company_id = '{LIVE_CONNECTIONS_UUID}'::uuid
        WHERE company_id IS NULL
    """)
    op.alter_column('batch_analyses', 'company_id', nullable=False)
    op.create_foreign_key('fk_batch_analyses_company_id', 'batch_analyses', 'companies', ['company_id'], ['id'])

    # ── 8. Add company_id to skills table ────────────────────────────────
    op.add_column('skills', sa.Column('company_id', UUID(as_uuid=True), nullable=True, index=True))
    op.execute(f"""
        UPDATE skills SET company_id = '{LIVE_CONNECTIONS_UUID}'::uuid
        WHERE company_id IS NULL
    """)
    op.alter_column('skills', 'company_id', nullable=False)
    op.create_foreign_key('fk_skills_company_id', 'skills', 'companies', ['company_id'], ['id'])

    # ── 9. Add company_id to audit_logs table (nullable for filtering) ───
    op.add_column('audit_logs', sa.Column('company_id', UUID(as_uuid=True), nullable=True, index=True))
    op.create_foreign_key('fk_audit_logs_company_id', 'audit_logs', 'companies', ['company_id'], ['id'])

    # ── 10. Update existing admin user to super_admin ────────────────────
    op.execute("""
        UPDATE users SET role = 'super_admin' WHERE role = 'admin'
    """)

    # ── 11. Update all non-super_admin users to belong to Live Connections
    op.execute(f"""
        UPDATE users SET company_id = '{LIVE_CONNECTIONS_UUID}'::uuid
        WHERE role != 'super_admin' AND company_id IS NULL
    """)

    # ── 12. Create composite indexes for efficient multi-tenant queries ───
    op.create_index('ix_candidates_company_id_created_at', 'candidates', ['company_id', 'created_at'], unique=False)
    op.create_index('ix_jobs_company_id_created_at', 'jobs', ['company_id', 'created_at'], unique=False)
    op.create_index('ix_analysis_results_company_id_created_at', 'analysis_results', ['company_id', 'created_at'], unique=False)
    op.create_index('ix_batch_analyses_company_id_created_at', 'batch_analyses', ['company_id', 'created_at'], unique=False)


def downgrade() -> None:
    # Drop indexes
    op.drop_index('ix_batch_analyses_company_id_created_at', 'batch_analyses')
    op.drop_index('ix_analysis_results_company_id_created_at', 'analysis_results')
    op.drop_index('ix_jobs_company_id_created_at', 'jobs')
    op.drop_index('ix_candidates_company_id_created_at', 'candidates')

    # Drop foreign keys and columns
    op.drop_constraint('fk_audit_logs_company_id', 'audit_logs', type_='foreignkey')
    op.drop_column('audit_logs', 'company_id')

    op.drop_constraint('fk_skills_company_id', 'skills', type_='foreignkey')
    op.drop_column('skills', 'company_id')

    op.drop_constraint('fk_batch_analyses_company_id', 'batch_analyses', type_='foreignkey')
    op.drop_column('batch_analyses', 'company_id')

    op.drop_constraint('fk_analysis_results_company_id', 'analysis_results', type_='foreignkey')
    op.drop_column('analysis_results', 'company_id')

    op.drop_constraint('fk_jobs_company_id', 'jobs', type_='foreignkey')
    op.drop_column('jobs', 'company_id')

    op.drop_constraint('fk_candidates_company_id', 'candidates', type_='foreignkey')
    op.drop_column('candidates', 'company_id')

    op.drop_constraint('fk_users_company_id', 'users', type_='foreignkey')
    op.drop_column('users', 'company_id')

    # Revert admin role and drop companies table
    op.execute("""
        UPDATE users SET role = 'admin' WHERE role = 'super_admin'
    """)

    op.drop_table('companies')
