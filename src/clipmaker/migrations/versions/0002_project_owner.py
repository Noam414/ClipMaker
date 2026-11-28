"""project owner and client ip (D-030)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-27 02:45:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('owner', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('client_ip', sa.String(length=64), nullable=True))
        batch_op.create_index('ix_projects_owner', ['owner'], unique=False)
        batch_op.create_index('ix_projects_client_ip', ['client_ip'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_index('ix_projects_client_ip')
        batch_op.drop_index('ix_projects_owner')
        batch_op.drop_column('client_ip')
        batch_op.drop_column('owner')
