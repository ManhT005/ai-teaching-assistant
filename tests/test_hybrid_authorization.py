from dataclasses import replace

import pytest

from app.models import ChannelBinding, Classroom
from app.retrieval import (
    ScopeAuthorizationError,
    resolve_authorized_scope,
    validate_authorized_scope,
)


def test_scope_comes_from_enabled_database_binding(authorized_contexts):
    sessions, contexts = authorized_contexts

    with sessions() as session:
        for code, context in contexts.items():
            assert context.class_code == code
            validate_authorized_scope(session, context)

        with pytest.raises(ScopeAuthorizationError):
            resolve_authorized_scope(session, channel="unverified", group_id="mock-ai-automation")


def test_forged_class_and_unissued_scope_fail_closed(authorized_contexts):
    sessions, contexts = authorized_contexts
    context = contexts["GS-KPI-01"]
    forged = replace(context, class_code="AUTO-N8N-01")
    unissued = replace(context, _proof=object())

    with sessions() as session:
        for bad in (forged, unissued, {"class_id": "AUTO-N8N-01"}):
            with pytest.raises(ScopeAuthorizationError):
                validate_authorized_scope(session, bad)


@pytest.mark.parametrize("change", ["inactive", "disabled", "rebound"])
def test_stale_binding_cannot_reuse_previous_scope(authorized_contexts, change):
    sessions, contexts = authorized_contexts
    context = contexts["GS-KPI-01"]

    with sessions.begin() as session:
        binding = session.get(ChannelBinding, context.binding_id)
        if change == "inactive":
            binding.active = False
        elif change == "disabled":
            session.get(Classroom, context.class_id).bot_enabled = False
        else:
            binding.class_id = contexts["AUTO-N8N-01"].class_id

    with sessions() as session, pytest.raises(ScopeAuthorizationError):
        validate_authorized_scope(session, context)
