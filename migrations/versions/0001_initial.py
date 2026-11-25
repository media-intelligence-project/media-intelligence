"""Store immutable publications, retry results and minimal import receipts."""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE publications (
            object_id text NOT NULL,
            version integer NOT NULL CHECK (version >= 0),
            object_type text NOT NULL,
            created_by text NOT NULL,
            body jsonb NOT NULL,
            PRIMARY KEY (object_id, version)
        )
    """)
    op.execute("CREATE INDEX publications_type ON publications (object_type)")
    op.execute("CREATE INDEX publications_origin ON publications (created_by)")
    op.execute("""CREATE TABLE request_retries (
        request_key text PRIMARY KEY,
        fingerprint text NOT NULL,
        response jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE TABLE import_records (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        package_id uuid NOT NULL,
        sender text NOT NULL,
        inserted integer NOT NULL,
        imported_at timestamptz NOT NULL DEFAULT now()
    )""")


def downgrade():
    op.execute("DROP TABLE import_records")
    op.execute("DROP TABLE request_retries")
    op.execute("DROP TABLE publications")
