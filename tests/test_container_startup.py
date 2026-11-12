"""Deployment boundaries: escaped credentials, fail-closed startup and curated demo evidence."""

from pathlib import Path

import pytest
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

from app.config import Settings, get_settings
from app.knowledge.source_manifest import load_manifest
from scripts.container_entrypoint import configure_database, main, validate_runtime


def test_container_database_uses_service_host_and_preserves_special_password():
    environment = {
        "POSTGRES_USER": "gos",
        "POSTGRES_PASSWORD": "special:@/?#%$ password",
        "POSTGRES_DB": "gos_smoke",
        "POSTGRES_HOST": "db",
        "POSTGRES_PORT": "5432",
        "DATABASE_URL": "postgresql+psycopg://wrong:wrong@localhost/wrong",
    }
    configure_database(environment)
    url = make_url(environment["DATABASE_URL"])
    assert url.host == "db"
    assert url.port == 5432
    assert url.password == environment["POSTGRES_PASSWORD"]
    assert url.database == "gos_smoke"


def test_compose_exec_configures_database_without_running_entrypoint(monkeypatch):
    values = {
        "POSTGRES_HOST": "db",
        "POSTGRES_PORT": "5432",
        "POSTGRES_USER": "gos",
        "POSTGRES_PASSWORD": "private:@%$password",
        "POSTGRES_DB": "gos_smoke",
        "DATABASE_URL": "postgresql+psycopg://wrong:wrong@localhost/wrong",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        url = make_url(settings.database_url)
        assert url.host == "db" and url.database == "gos_smoke"
        assert url.password == values["POSTGRES_PASSWORD"]
        assert values["POSTGRES_PASSWORD"] not in repr(settings)
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("missing", ["POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"])
def test_container_rejects_incomplete_database_configuration(missing):
    environment = dict.fromkeys(
        ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "POSTGRES_HOST"), "value"
    )
    environment.pop(missing)
    with pytest.raises(ValueError, match=missing):
        configure_database(environment)


@pytest.mark.parametrize("webhook,operator", [("", "o" * 32), ("w" * 32, "short"), ("x" * 32,) * 2])
def test_container_rejects_missing_short_or_shared_tokens(monkeypatch, webhook, operator):
    settings = Settings(_env_file=None, mock_webhook_token=webhook, operator_api_token=operator)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    with pytest.raises(ValueError):
        validate_runtime()


def test_container_accepts_distinct_offline_tokens(monkeypatch):
    settings = Settings(
        _env_file=None,
        mock_webhook_token="w" * 32,
        operator_api_token="o" * 32,
        llm_provider="fake",
    )
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    assert validate_runtime() is settings


def test_container_startup_does_not_print_database_secrets(monkeypatch, capsys):
    marker = "private-password-marker"
    monkeypatch.setattr(
        "scripts.container_entrypoint.configure_database",
        lambda _: (_ for _ in ()).throw(ValueError(marker)),
    )
    with pytest.raises(SystemExit) as failure:
        main(["bootstrap"])
    assert failure.value.code == 1
    output = capsys.readouterr()
    assert marker not in output.out + output.err
    assert "Container startup failed" in output.err


def test_env_template_covers_all_application_settings_without_duplicate_keys():
    path = Path(".env.example")
    keys = [
        line.split("=", 1)[0]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(keys) == len(set(keys))
    assert {key.upper() for key in Settings.model_fields} <= set(keys)
    values = dotenv_values(path)
    assert all(value is not None for value in values.values())
    assert values["MOCK_WEBHOOK_TOKEN"] == ""
    assert values["OPERATOR_API_TOKEN"] == ""


def test_packaged_manifest_preserves_scope_and_pending_source_status():
    path = Path("data/samples/docker-manifest.json")
    sections = load_manifest(path)
    assert len(sections) == 9
    assert {section.class_scope for section in sections} == {"GS-KPI-01", "AUTO-N8N-01", "COMMON"}
    pending = [section for section in sections if section.review_status == "pending"]
    assert len(pending) == 1
    assert pending[0].record_id == "DEMO-AUTO-003"
    assert all(section.review_decision is None for section in sections)


@pytest.fixture
def bootstrap_store(rag_engine):
    from sqlalchemy.orm import sessionmaker
    from test_ingestion_db import FakeEmbedding

    from app.knowledge.store import KnowledgeStore

    return KnowledgeStore(
        sessionmaker(rag_engine, expire_on_commit=False), FakeEmbedding(), Settings(_env_file=None)
    )


def test_bootstrap_preserves_updated_and_revoked_sources(bootstrap_store):
    from test_ingestion_db import section

    from scripts.container_entrypoint import bootstrap_knowledge

    initial = [section(), section(doc_id="OTHER")]
    bootstrap_knowledge(bootstrap_store, initial)
    bootstrap_store.ingest([section("GS-02 Updated operator source")])
    bootstrap_store.revoke("OTHER", "Operator withdrawal")
    before = bootstrap_store.status()
    bootstrap_knowledge(bootstrap_store, initial)
    assert bootstrap_store.status() == before
    assert any(row["active"] and row["version"] == 2 for row in before)
    assert any(row["status"] == "REVOKED" for row in before)


def test_bootstrap_recovers_failed_initial_ingestion(bootstrap_store, monkeypatch):
    from test_ingestion_db import section

    from scripts.container_entrypoint import bootstrap_knowledge

    encode = bootstrap_store.embeddings.encode_passages

    def unavailable(texts):
        raise RuntimeError("temporary embedding failure")

    monkeypatch.setattr(bootstrap_store.embeddings, "encode_passages", unavailable)
    initial = [section(), section(doc_id="OTHER")]
    with pytest.raises(RuntimeError):
        bootstrap_knowledge(bootstrap_store, initial)
    monkeypatch.setattr(bootstrap_store.embeddings, "encode_passages", encode)
    bootstrap_knowledge(bootstrap_store, initial)
    active = {row["doc_id"] for row in bootstrap_store.status() if row["active"]}
    assert active == {"DOC-GS-01", "OTHER"}
