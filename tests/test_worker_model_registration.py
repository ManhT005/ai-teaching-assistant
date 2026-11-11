"""Standalone worker imports must register every foreign-key target they write."""

import subprocess
import sys


def test_standalone_event_worker_registers_ticket_foreign_keys():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from scripts.run_event_worker import build_worker; "
                "from app.models import Ticket; "
                "assert 'staff_actors' in Ticket.metadata.tables; "
                "assert all(foreign_key.column is not None "
                "for foreign_key in Ticket.__table__.foreign_keys)"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
