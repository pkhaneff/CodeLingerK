"""add_auth_and_rbac_tables

Revision ID: 7397facdb92c
Revises: 005_repository_is_active
Create Date: 2026-06-29 13:13:20.463821

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '7397facdb92c'
down_revision: Union[str, Sequence[str], None] = '005_repository_is_active'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    # 1. Tạo bảng roles nếu chưa tồn tại
    if 'roles' not in tables:
        op.create_table(
            'roles',
            sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
            sa.Column('authority', sa.String(length=10), nullable=False),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('authority')
        )
        # Seed sẵn dữ liệu vai trò
        op.execute("INSERT INTO roles (id, authority, name) VALUES ('c12c5bda-4ad9-4d6b-9c76-d98c56faef69', '1', 'Administrator')")
        op.execute("INSERT INTO roles (id, authority, name) VALUES ('2d2b5fda-3bd9-4a6b-9d76-e98c56faf873', '2', 'User')")
    else:
        # Seed nếu chưa có bản ghi trùng authority
        op.execute("INSERT INTO roles (id, authority, name) VALUES ('c12c5bda-4ad9-4d6b-9c76-d98c56faef69', '1', 'Administrator') ON CONFLICT (authority) DO NOTHING")
        op.execute("INSERT INTO roles (id, authority, name) VALUES ('2d2b5fda-3bd9-4a6b-9d76-e98c56faf873', '2', 'User') ON CONFLICT (authority) DO NOTHING")

    # 2. Tạo bảng blacklisted_tokens nếu chưa tồn tại
    if 'blacklisted_tokens' not in tables:
        op.create_table(
            'blacklisted_tokens',
            sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
            sa.Column('token', sa.String(length=500), nullable=False),
            sa.Column('blacklisted_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('user_id', sa.UUID(as_uuid=False), nullable=False),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('token')
        )

    # 3. Cập nhật các cột trong bảng users
    columns_in_users = [c['name'] for c in inspector.get_columns('users')]

    if 'username' not in columns_in_users:
        op.add_column('users', sa.Column('username', sa.String(length=150), nullable=True))
    if 'email' not in columns_in_users:
        op.add_column('users', sa.Column('email', sa.String(length=254), nullable=True))
    if 'hashed_password' not in columns_in_users:
        op.add_column('users', sa.Column('hashed_password', sa.String(length=128), nullable=True))
    if 'last_logout' not in columns_in_users:
        op.add_column('users', sa.Column('last_logout', sa.DateTime(timezone=True), nullable=True))
    if 'is_staff' not in columns_in_users:
        op.add_column('users', sa.Column('is_staff', sa.Boolean(), nullable=False, server_default='false'))
    if 'is_superuser' not in columns_in_users:
        op.add_column('users', sa.Column('is_superuser', sa.Boolean(), nullable=False, server_default='false'))
    if 'role_id' not in columns_in_users:
        op.add_column('users', sa.Column('role_id', sa.UUID(as_uuid=False), nullable=True))

    # Cập nhật các cột github_id, github_username thành nullable=True
    op.alter_column('users', 'github_id',
               existing_type=sa.BIGINT(),
               nullable=True)
    op.alter_column('users', 'github_username',
               existing_type=sa.VARCHAR(length=255),
               nullable=True)

    # Tạo ràng buộc Unique và Foreign Key nếu chưa có
    # Kiểm tra xem unique constraint cho email và username đã có chưa
    constraints = inspector.get_unique_constraints('users')
    
    # Alembic tự tạo tên ngẫu nhiên nếu truyền None, nên dùng try-except hoặc kiểm tra tên
    # Để đơn giản, nếu các cột vừa được add hoặc chưa có unique index thì add unique
    try:
        op.create_unique_constraint('uq_users_email', 'users', ['email'])
    except Exception:
        pass
        
    try:
        op.create_unique_constraint('uq_users_username', 'users', ['username'])
    except Exception:
        pass

    fk_constraints = inspector.get_foreign_keys('users')
    if not any('role_id' in f['referred_columns'] or 'role_id' in f['constrained_columns'] for f in fk_constraints):
        try:
            op.create_foreign_key('fk_users_role_id_roles', 'users', 'roles', ['role_id'], ['id'], ondelete='SET NULL')
        except Exception:
            pass

    # Phục vụ việc điều chỉnh kiểu dữ liệu của review_comments.github_comment_id (nếu có)
    try:
        op.alter_column('review_comments', 'github_comment_id',
                   existing_type=sa.INTEGER(),
                   type_=sa.BigInteger(),
                   existing_nullable=True)
    except Exception:
        pass


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    
    # Drop foreign key and columns from users
    try:
        op.drop_constraint('fk_users_role_id_roles', 'users', type_='foreignkey')
    except Exception:
        pass
    try:
        op.drop_constraint('uq_users_username', 'users', type_='unique')
    except Exception:
        pass
    try:
        op.drop_constraint('uq_users_email', 'users', type_='unique')
    except Exception:
        pass

    columns_in_users = [c['name'] for c in inspector.get_columns('users')]
    for col in ['username', 'email', 'hashed_password', 'last_logout', 'is_staff', 'is_superuser', 'role_id']:
        if col in columns_in_users:
            op.drop_column('users', col)

    # Drop blacklisted_tokens and roles tables
    tables = inspector.get_table_names()
    if 'blacklisted_tokens' in tables:
        op.drop_table('blacklisted_tokens')
    if 'roles' in tables:
        op.drop_table('roles')
