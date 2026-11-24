"""Keep uploaded source files in the database so they survive a wiped disk."""
from alembic import op
import sqlalchemy as sa

revision = "20261121_0018"
down_revision = "20261107_0017"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "workspace_source_blobs" in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "workspace_source_blobs",
        sa.Column("workspace_id", sa.String(100), sa.ForeignKey("workspace_records.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("report_metadata", sa.LargeBinary(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workspace_source_blobs_tenant_id", "workspace_source_blobs", ["tenant_id"])


def downgrade():
    op.drop_index("ix_workspace_source_blobs_tenant_id", table_name="workspace_source_blobs")
    op.drop_table("workspace_source_blobs")
