"""Staff-scoped teacher response and reviewed FAQ workflow (PostgreSQL)."""

from alembic import op

revision = "0009_teacher_faq_workflow"
down_revision = "0008_contextual_teaching"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE staff_actors (
            id SERIAL NOT NULL, 
            alias VARCHAR(100) NOT NULL, 
            active BOOLEAN DEFAULT 'true' NOT NULL, 
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
            PRIMARY KEY (id), 
            UNIQUE (alias)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE staff_class_grants (
            id SERIAL NOT NULL, 
            actor_id INTEGER NOT NULL, 
            class_id INTEGER NOT NULL, 
            role VARCHAR(20) NOT NULL, 
            PRIMARY KEY (id), 
            UNIQUE (actor_id, class_id, role), 
            CONSTRAINT ck_staff_role CHECK (role IN ('TEACHER', 'FAQ_REVIEWER', 'OPERATOR')), 
            FOREIGN KEY(actor_id) REFERENCES staff_actors (id), 
            FOREIGN KEY(class_id) REFERENCES classes (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE staff_credentials (
            id SERIAL NOT NULL, 
            actor_id INTEGER NOT NULL, 
            token_hash VARCHAR(64) NOT NULL, 
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
            revoked_at TIMESTAMP WITH TIME ZONE, 
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
            PRIMARY KEY (id), 
            FOREIGN KEY(actor_id) REFERENCES staff_actors (id), 
            UNIQUE (token_hash)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ticket_actions (
            id SERIAL NOT NULL, 
            ticket_id INTEGER NOT NULL, 
            actor_id INTEGER NOT NULL, 
            action VARCHAR(40) NOT NULL, 
            from_state VARCHAR(25) NOT NULL, 
            to_state VARCHAR(25) NOT NULL, 
            expected_version INTEGER NOT NULL, 
            action_key VARCHAR(100) NOT NULL, 
            request_hash VARCHAR(64) NOT NULL, 
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
            PRIMARY KEY (id), 
            UNIQUE (ticket_id, action_key), 
            FOREIGN KEY(ticket_id) REFERENCES tickets (id), 
            FOREIGN KEY(actor_id) REFERENCES staff_actors (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE teacher_replies (
            id SERIAL NOT NULL, 
            ticket_id INTEGER NOT NULL, 
            revision INTEGER NOT NULL, 
            body TEXT NOT NULL, 
            body_hash VARCHAR(64) NOT NULL, 
            author_actor_id INTEGER NOT NULL, 
            approver_actor_id INTEGER, 
            approval_credential_id INTEGER, 
            approved_at TIMESTAMP WITH TIME ZONE, 
            status VARCHAR(20) NOT NULL, 
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
            PRIMARY KEY (id), 
            CONSTRAINT ck_teacher_reply_status CHECK (status IN ('DRAFT', 'APPROVED', 'SENT')), 
            UNIQUE (ticket_id), 
            FOREIGN KEY(ticket_id) REFERENCES tickets (id), 
            FOREIGN KEY(author_actor_id) REFERENCES staff_actors (id), 
            FOREIGN KEY(approver_actor_id) REFERENCES staff_actors (id), 
            FOREIGN KEY(approval_credential_id) REFERENCES staff_credentials (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE teacher_reply_outbox (
            id SERIAL NOT NULL, 
            reply_id INTEGER NOT NULL, 
            message_key VARCHAR(160) NOT NULL, 
            class_id INTEGER NOT NULL, 
            binding_id INTEGER NOT NULL, 
            channel VARCHAR(30) NOT NULL, 
            group_id VARCHAR(160) NOT NULL, 
            approved_revision INTEGER NOT NULL, 
            approved_hash VARCHAR(64) NOT NULL, 
            status VARCHAR(20) NOT NULL, 
            attempts INTEGER NOT NULL, 
            lease_token VARCHAR(36), 
            lease_expires_at TIMESTAMP WITH TIME ZONE, 
            next_attempt_at TIMESTAMP WITH TIME ZONE, 
            last_error_code VARCHAR(80), 
            sent_at TIMESTAMP WITH TIME ZONE, 
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
            PRIMARY KEY (id), 
            CONSTRAINT ck_teacher_outbox_status CHECK (status IN ('PENDING', 'SENDING', 'RETRY_WAIT', 'SENT', 'DEAD', 'SUPPRESSED')), 
            UNIQUE (reply_id), 
            FOREIGN KEY(reply_id) REFERENCES teacher_replies (id), 
            UNIQUE (message_key), 
            FOREIGN KEY(class_id) REFERENCES classes (id), 
            FOREIGN KEY(binding_id) REFERENCES channel_bindings (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE teacher_mock_receipts (
            id SERIAL NOT NULL, 
            teacher_outbox_id INTEGER NOT NULL, 
            message_key VARCHAR(160) NOT NULL, 
            body TEXT NOT NULL, 
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
            PRIMARY KEY (id), 
            UNIQUE (teacher_outbox_id), 
            FOREIGN KEY(teacher_outbox_id) REFERENCES teacher_reply_outbox (id), 
            UNIQUE (message_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE faq_candidates (
            id SERIAL NOT NULL, 
            ticket_id INTEGER NOT NULL, 
            reply_id INTEGER NOT NULL, 
            class_id INTEGER NOT NULL, 
            author_actor_id INTEGER NOT NULL, 
            creation_key VARCHAR(100) NOT NULL, 
            revision INTEGER NOT NULL, 
            status VARCHAR(20) NOT NULL, 
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
            PRIMARY KEY (id), 
            CONSTRAINT ck_faq_status CHECK (status IN ('DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED', 'INDEXING', 'FAILED', 'PUBLISHED', 'REVOKED')), 
            FOREIGN KEY(ticket_id) REFERENCES tickets (id), 
            FOREIGN KEY(reply_id) REFERENCES teacher_replies (id), 
            FOREIGN KEY(class_id) REFERENCES classes (id), 
            FOREIGN KEY(author_actor_id) REFERENCES staff_actors (id), 
            UNIQUE (creation_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE faq_revisions (
            id SERIAL NOT NULL, 
            faq_id INTEGER NOT NULL, 
            revision INTEGER NOT NULL, 
            question TEXT NOT NULL, 
            answer TEXT NOT NULL, 
            topics JSONB NOT NULL, 
            source_references JSONB NOT NULL, 
            body_hash VARCHAR(64) NOT NULL, 
            reviewer_actor_id INTEGER, 
            reviewer_credential_id INTEGER, 
            reviewed_at TIMESTAMP WITH TIME ZONE, 
            review_reason VARCHAR(500), 
            decision VARCHAR(20), 
            submitted_at TIMESTAMP WITH TIME ZONE, 
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
            PRIMARY KEY (id), 
            UNIQUE (faq_id, revision), 
            FOREIGN KEY(faq_id) REFERENCES faq_candidates (id), 
            FOREIGN KEY(reviewer_actor_id) REFERENCES staff_actors (id), 
            FOREIGN KEY(reviewer_credential_id) REFERENCES staff_credentials (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE faq_publications (
            id SERIAL NOT NULL, 
            faq_id INTEGER NOT NULL, 
            revision INTEGER NOT NULL, 
            logical_doc_id VARCHAR(160) NOT NULL, 
            document_id INTEGER, 
            artifact_checksum VARCHAR(64), 
            manifest_checksum VARCHAR(64), 
            status VARCHAR(20) NOT NULL, 
            last_error_code VARCHAR(80), 
            published_at TIMESTAMP WITH TIME ZONE, 
            lease_token VARCHAR(36), 
            lease_expires_at TIMESTAMP WITH TIME ZONE, 
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
            PRIMARY KEY (id), 
            UNIQUE (faq_id, revision), 
            FOREIGN KEY(faq_id) REFERENCES faq_candidates (id), 
            FOREIGN KEY(document_id) REFERENCES documents (id)
        )
        """
    )
    op.execute(
        """
        ALTER TABLE tickets
         ADD COLUMN version integer NOT NULL DEFAULT 1 CHECK (version > 0),
         ADD COLUMN assigned_teacher_id integer REFERENCES staff_actors(id),
         ADD COLUMN assigned_at timestamptz,
         ADD COLUMN resolved_at timestamptz,
         ADD COLUMN closed_at timestamptz,
         ADD COLUMN last_action_at timestamptz,
         ADD COLUMN closure_reason varchar(500),
         ADD CONSTRAINT ck_ticket_workflow_status CHECK (status IN ('OPEN', 'ASSIGNED', 'IN_REVIEW', 'REPLY_QUEUED', 'RESOLVED', 'CLOSED_NO_REPLY'))
        """
    )
    op.execute(
        "CREATE INDEX ix_teacher_outbox_due ON teacher_reply_outbox(status, next_attempt_at, id)"
    )
    op.execute("CREATE INDEX ix_ticket_staff_queue ON tickets(class_id, status, id)")
    op.execute(
        "CREATE FUNCTION reject_workflow_audit_mutation() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'Workflow audit is append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER ticket_actions_append_only BEFORE UPDATE OR DELETE ON ticket_actions FOR EACH ROW EXECUTE FUNCTION reject_workflow_audit_mutation()"
    )
    op.execute("""
        CREATE FUNCTION protect_workflow_content() RETURNS trigger AS $$
        BEGIN
            IF TG_TABLE_NAME = 'teacher_replies' THEN
                IF OLD.status <> 'DRAFT' AND
                   ROW(NEW.body, NEW.body_hash, NEW.revision, NEW.author_actor_id,
                       NEW.approver_actor_id, NEW.approval_credential_id, NEW.approved_at)
                   IS DISTINCT FROM
                   ROW(OLD.body, OLD.body_hash, OLD.revision, OLD.author_actor_id,
                       OLD.approver_actor_id, OLD.approval_credential_id, OLD.approved_at) THEN
                    RAISE EXCEPTION 'Approved reply is immutable';
                END IF;
            ELSE
                IF OLD.submitted_at IS NOT NULL AND
                   ROW(NEW.question, NEW.answer, NEW.topics, NEW.source_references,
                       NEW.body_hash, NEW.revision, NEW.faq_id, NEW.submitted_at)
                   IS DISTINCT FROM
                   ROW(OLD.question, OLD.answer, OLD.topics, OLD.source_references,
                       OLD.body_hash, OLD.revision, OLD.faq_id, OLD.submitted_at) THEN
                    RAISE EXCEPTION 'Submitted FAQ is immutable';
                END IF;
                IF OLD.decision IS NOT NULL AND
                   ROW(NEW.decision, NEW.reviewer_actor_id, NEW.reviewer_credential_id,
                       NEW.reviewed_at, NEW.review_reason)
                   IS DISTINCT FROM
                   ROW(OLD.decision, OLD.reviewer_actor_id, OLD.reviewer_credential_id,
                       OLD.reviewed_at, OLD.review_reason) THEN
                    RAISE EXCEPTION 'FAQ review is immutable';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute(
        "CREATE TRIGGER protect_teacher_reply BEFORE UPDATE ON teacher_replies FOR EACH ROW EXECUTE FUNCTION protect_workflow_content()"
    )
    op.execute(
        "CREATE TRIGGER protect_faq_revision BEFORE UPDATE ON faq_revisions FOR EACH ROW EXECUTE FUNCTION protect_workflow_content()"
    )


def downgrade():
    # Retain source history but withdraw governance-dependent FAQs on rollback.
    op.execute(
        "UPDATE documents SET active=false, index_status='REVOKED', revoked_at=now(), revocation_reason='FAQ_WORKFLOW_ROLLBACK' WHERE logical_key IN (SELECT logical_doc_id FROM faq_publications)"
    )
    op.execute("DROP TRIGGER ticket_actions_append_only ON ticket_actions")
    op.execute("DROP TRIGGER protect_teacher_reply ON teacher_replies")
    op.execute("DROP TRIGGER protect_faq_revision ON faq_revisions")
    op.execute("DROP FUNCTION protect_workflow_content()")
    op.execute("DROP FUNCTION reject_workflow_audit_mutation()")
    op.execute("DROP INDEX ix_ticket_staff_queue")
    op.execute(
        "ALTER TABLE tickets DROP CONSTRAINT ck_ticket_workflow_status, DROP COLUMN version, DROP COLUMN assigned_teacher_id, DROP COLUMN assigned_at, DROP COLUMN resolved_at, DROP COLUMN closed_at, DROP COLUMN last_action_at, DROP COLUMN closure_reason"
    )
    op.drop_table("faq_publications")
    op.drop_table("faq_revisions")
    op.drop_table("faq_candidates")
    op.drop_table("teacher_mock_receipts")
    op.drop_table("teacher_reply_outbox")
    op.drop_table("teacher_replies")
    op.drop_table("ticket_actions")
    op.drop_table("staff_credentials")
    op.drop_table("staff_class_grants")
    op.drop_table("staff_actors")
