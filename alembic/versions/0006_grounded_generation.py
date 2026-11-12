"""Add grounding audit and serialize mutually exclusive event outcomes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_grounded_generation"
down_revision = "0005_hybrid_retrieval"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("processing_started_at", "finished_at"):
        op.add_column("events", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.add_column("events", sa.Column("last_error_code", sa.String(80), nullable=True))
    op.add_column("answers", sa.Column("validation_summary", postgresql.JSONB(), nullable=True))
    op.add_column("answers", sa.Column("provider_request_id", sa.String(255), nullable=True))
    op.add_column("tickets", sa.Column("reason_code", sa.String(80), nullable=True))
    op.add_column("tickets", sa.Column("assigned_to", sa.String(160), nullable=True))
    op.add_column("tickets", sa.Column("evidence_summary", postgresql.JSONB(), nullable=True))
    op.add_column("model_usage", sa.Column("request_id", sa.String(255), nullable=True))
    for name in ("prompt_tokens", "completion_tokens"):
        op.alter_column("model_usage", name, existing_type=sa.Integer(), nullable=True)

    # Existing rows are retained. Conflicting legacy outcomes require explicit repair.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM answers a JOIN tickets t ON a.event_id = t.event_id) THEN
                RAISE EXCEPTION 'Conflicting legacy event outcomes require operator review';
            END IF;
        END $$
    """)
    op.execute("""
        CREATE FUNCTION guard_event_outcome() RETURNS trigger AS $$
        BEGIN
            PERFORM id FROM events WHERE id = NEW.event_id FOR UPDATE;
            IF TG_TABLE_NAME = 'answers' THEN
                IF EXISTS (SELECT 1 FROM tickets WHERE event_id = NEW.event_id) THEN
                    RAISE EXCEPTION 'Event already has a ticket' USING ERRCODE = '23514';
                END IF;
            ELSE
                IF EXISTS (SELECT 1 FROM answers WHERE event_id = NEW.event_id) THEN
                    RAISE EXCEPTION 'Event already has an answer' USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    for table in ("answers", "tickets"):
        op.execute(f"""
            CREATE TRIGGER guard_{table}_outcome
            BEFORE INSERT OR UPDATE OF event_id ON {table}
            FOR EACH ROW EXECUTE FUNCTION guard_event_outcome()
        """)


def downgrade():
    for table in ("answers", "tickets"):
        op.execute(f"DROP TRIGGER guard_{table}_outcome ON {table}")
    op.execute("DROP FUNCTION guard_event_outcome()")
    for name in ("prompt_tokens", "completion_tokens"):
        # Downgrade preserves unknown usage as zero for the legacy non-null schema.
        op.execute(sa.text(f"UPDATE model_usage SET {name} = 0 WHERE {name} IS NULL"))
        op.alter_column("model_usage", name, existing_type=sa.Integer(), nullable=False)
    for table, names in (
        ("model_usage", ("request_id",)),
        ("tickets", ("evidence_summary", "assigned_to", "reason_code")),
        ("answers", ("provider_request_id", "validation_summary")),
        ("events", ("last_error_code", "finished_at", "processing_started_at")),
    ):
        for name in names:
            op.drop_column(table, name)
