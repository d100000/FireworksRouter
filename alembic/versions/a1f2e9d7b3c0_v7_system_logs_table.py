"""v7: system_logs table

Revision ID: a1f2e9d7b3c0
Revises: 642784f0df90
Create Date: 2026-05-23 19:30:00.000000

新增 system_logs 表用于持久化应用层日志（loguru 自定义 sink 入库），
方便 UI 查询/批量清理，并配合 _cleaner 按保留期自动 purge。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1f2e9d7b3c0'
down_revision: Union[str, None] = '642784f0df90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'system_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('level', sa.String(length=16), nullable=False),
        sa.Column('module', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('function', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('line', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('request_id', sa.String(length=64), nullable=True),
        sa.Column('extra', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('system_logs', schema=None) as batch_op:
        batch_op.create_index('ix_system_logs_timestamp_desc', ['timestamp'], unique=False)
        batch_op.create_index('ix_system_logs_level_timestamp', ['level', 'timestamp'], unique=False)
        batch_op.create_index('ix_system_logs_request_id', ['request_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('system_logs', schema=None) as batch_op:
        batch_op.drop_index('ix_system_logs_request_id')
        batch_op.drop_index('ix_system_logs_level_timestamp')
        batch_op.drop_index('ix_system_logs_timestamp_desc')
    op.drop_table('system_logs')
