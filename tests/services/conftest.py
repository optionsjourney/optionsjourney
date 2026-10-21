import pytest

from optionsjourney.db import make_engine, make_session_factory, migrate


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    return make_session_factory(engine)
