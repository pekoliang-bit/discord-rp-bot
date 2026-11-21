"""Does not access Turso, Discord, or a real database."""
import pytest
from core import turso_backend


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.setenv('TURSO_DATABASE_URL', 'libsql://example.test')
    monkeypatch.setenv('TURSO_AUTH_TOKEN', 'test-only-secret')
    monkeypatch.delenv('RP_TURSO_MODE', raising=False)
    monkeypatch.delenv('RP_TURSO_PRODUCTION_ACK', raising=False)


def test_missing_mode_blocks(cloud):
    with pytest.raises(RuntimeError, match='RP_TURSO_MODE'):
        turso_backend.require_staging_gate()


def test_staging_allows_database_but_not_production(cloud, monkeypatch):
    monkeypatch.setenv('RP_TURSO_MODE', 'staging')
    turso_backend.require_staging_gate()
    with pytest.raises(RuntimeError, match='production'):
        turso_backend.require_production_gate()


def test_production_needs_ack(cloud, monkeypatch):
    monkeypatch.setenv('RP_TURSO_MODE', 'production')
    with pytest.raises(RuntimeError, match='RP_TURSO_PRODUCTION_ACK'):
        turso_backend.require_staging_gate()


def test_production_explicit_opt_in(cloud, monkeypatch):
    monkeypatch.setenv('RP_TURSO_MODE', 'production')
    monkeypatch.setenv('RP_TURSO_PRODUCTION_ACK', 'I_ACCEPT_FRESH_DATABASE')
    turso_backend.require_production_gate()
    turso_backend.require_staging_gate()


def test_missing_credentials_never_falls_back(monkeypatch):
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.delenv('TURSO_DATABASE_URL', raising=False)
    monkeypatch.delenv('TURSO_AUTH_TOKEN', raising=False)
    monkeypatch.setenv('RP_TURSO_MODE', 'production')
    with pytest.raises(RuntimeError, match='Token'):
        turso_backend.require_production_gate()
