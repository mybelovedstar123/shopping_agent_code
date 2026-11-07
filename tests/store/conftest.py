import pytest

from kyo.store.db import make_engine, make_sessions
from kyo.store.models import Base


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def sessions(engine):
    return make_sessions(engine)
