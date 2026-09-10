"""
Database connection and session management.

WHY THIS IS ITS OWN FILE
------------------------
Every part of the system that touches the database imports from here, so the
connection string exists in exactly one place. When SQLite becomes PostgreSQL,
one line changes and nothing else does. Scattering create_engine() calls across
the codebase is how a "mechanical" migration turns into a two-day job.

WHY sessionmaker RATHER THAN A GLOBAL SESSION
---------------------------------------------
A Session is a unit of work, not a connection pool. It holds an identity map of
everything it has loaded and is NOT thread-safe. Sharing one across the FastAPI
request handlers we add later would leak one request's objects into another's.
The factory hands out a fresh, short-lived session per unit of work.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base

BASE = Path(__file__).resolve().parents[2]
DB_PATH = BASE / "data" / "candidates.db"

# PRODUCTION UPGRADE: swap for
#   postgresql+psycopg://user:pass@host/dbname
# Nothing else in the project needs to change -- no SQLite-specific types are
# used anywhere in models.py.
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    # SQLite refuses cross-thread use by default. Our loader and the FastAPI
    # app both use threads, and each gets its own Session, so the check is
    # unnecessary here. On PostgreSQL this argument disappears entirely.
    connect_args={"check_same_thread": False},
    echo=False,   # set True to watch the SQL being generated; good for learning
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(drop: bool = False) -> None:
    """Creates the tables. With drop=True, recreates from scratch.

    Dropping is acceptable here only because the data is regenerable in
    minutes. PRODUCTION UPGRADE: Alembic migrations, so a schema change alters
    the tables instead of destroying them.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if drop:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


@contextmanager
def get_session() -> Session:
    """Session that commits on success and rolls back on any exception.

    Without the rollback, a failed load can leave half a candidate committed --
    a Candidate row with no skills, which then silently fails every filter.
    Partial writes are worse than no writes.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()