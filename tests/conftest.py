from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import database
from app.database import Base, get_db
from app.main import app
from app.models.call import CallStatus
from app.routes import calls as calls_route
from app.routes import voice as voice_route
from app.services.call_summary import CallSummary


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_path = (tmp_path / "test.db").resolve()
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    database_url = Path(engine.url.database).resolve()
    if database_url.parent != tmp_path.resolve():
        pytest.fail(f"Test database is outside tmp_path: {database_url}")
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db() -> Generator[Session, None, None]:
        db = test_session()
        try:
            yield db
        finally:
            db.close()

    def no_response_summary(_transcript) -> CallSummary:
        return CallSummary(status=CallStatus.NO_RESPONSE)

    monkeypatch.setattr(database, "SessionLocal", test_session)
    monkeypatch.setattr(voice_route, "SessionLocal", test_session)
    monkeypatch.setitem(app.dependency_overrides, get_db, override_get_db)
    monkeypatch.setattr(calls_route, "generate_summary", no_response_summary)
    yield test_session
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def db_session(isolated_database) -> Generator[Session, None, None]:
    session = isolated_database()
    try:
        yield session
    finally:
        session.close()
