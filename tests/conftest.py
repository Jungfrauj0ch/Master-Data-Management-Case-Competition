import pytest
from pathlib import Path
from uuid import uuid4
from sqlalchemy import create_engine, event

from database import connection
from services.access_service import Actor


@pytest.fixture(autouse=True)
def database(monkeypatch):
    # Use an existing project directory: Windows sandbox ACLs can reject the
    # private directories created by pytest's tmp_path fixture.
    database_path = Path(__file__).parent / f".test-{uuid4().hex}.db"
    original_engine = connection.engine
    engine = create_engine(f"sqlite:///{database_path.as_posix()}",
                           connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def foreign_keys(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    monkeypatch.setattr(connection, "engine", engine)
    connection.SessionLocal.configure(bind=engine)
    monkeypatch.delenv("MDM_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("MDM_AUTH_MODE", "demo")
    connection.initialize_database()
    yield engine
    connection.SessionLocal.configure(bind=original_engine)
    engine.dispose()
    database_path.unlink(missing_ok=True)


@pytest.fixture
def requestor():
    return Actor("alice", frozenset({"requestor"}))
