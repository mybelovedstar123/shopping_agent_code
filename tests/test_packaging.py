"""The packaged application has what it needs, without running Docker."""

import tomllib
from pathlib import Path

import pytest

import kyo.__main__ as entry

ROOT = Path(__file__).parents[1]


def test_web_files_are_packaged():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    data = project["tool"]["setuptools"]["package-data"]["kyo.web"]
    assert "templates/*.html" in data and "static/*" in data
    assert list((ROOT / "kyo/web/templates").glob("*.html"))
    assert list((ROOT / "kyo/web/static").iterdir())


def test_every_package_folder_is_a_package():
    for folder in (ROOT / "kyo").rglob("*"):
        if folder.is_dir() and any(folder.glob("*.py")):
            assert (folder / "__init__.py").exists(), folder


def test_image_runs_as_a_non_root_user_and_has_a_healthcheck():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER kyo" in dockerfile and "HEALTHCHECK" in dockerfile
    assert dockerfile.index("USER kyo") < dockerfile.index("CMD [")


def test_no_secret_is_baked_into_the_image_or_compose_file():
    for name in ("Dockerfile", "compose.yaml"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "sk-" not in text
    assert 'KYO_MODEL_API_KEY: "${KYO_MODEL_API_KEY:-}"' in (ROOT / "compose.yaml").read_text()


def test_compose_publishes_only_to_this_machine_by_default():
    assert '"127.0.0.1:${KYO_PUBLISH_PORT:-8000}:8000"' in (ROOT / "compose.yaml").read_text(
        encoding="utf-8"
    )


def test_only_what_the_image_needs_enters_the_build():
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ignore[0] == "*" and "!kyo" in ignore


@pytest.fixture
def started(monkeypatch, tmp_path):
    """Run the entry point without really opening a port."""
    calls = {}

    def fake_run(app, host, port, log_level):
        calls.update(app=app, host=host, port=port)

    monkeypatch.setattr(entry.uvicorn, "run", fake_run)
    monkeypatch.setenv("KYO_DATABASE_URL", f"sqlite:///{tmp_path / 'main.sqlite3'}")
    yield calls
    calls["app"].state.engine.dispose()


def test_entry_point_seeds_and_serves(started, monkeypatch):
    from kyo.store.db import transaction
    from kyo.store.models import Product

    monkeypatch.setenv("KYO_HOST", "0.0.0.0")
    monkeypatch.setenv("KYO_PORT", "9000")
    entry.main()
    assert started["host"] == "0.0.0.0" and started["port"] == 9000
    with transaction(started["app"].state.sessions) as session:
        assert session.query(Product).count() >= 120


def test_entry_point_can_skip_seeding(started, monkeypatch):
    from kyo.store.db import transaction
    from kyo.store.models import Product

    monkeypatch.setenv("KYO_SEED", "0")
    entry.main()
    with transaction(started["app"].state.sessions) as session:
        assert session.query(Product).count() == 0
