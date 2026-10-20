from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


def _database_url() -> str:
    url = settings.DATABASE_URL
    if not url or url == "sqlite:///./vaani.db":
        url = f"sqlite:///{Path(__file__).resolve().parent.parent / 'vaani.db'}"
    if url.startswith(("postgres://", "postgresql://")):
        return url.replace("postgres://", "postgresql+psycopg://", 1).replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
    return url


DATABASE_URL = _database_url()
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(
    DATABASE_URL, connect_args=connect_args, pool_pre_ping=True, pool_recycle=300
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
