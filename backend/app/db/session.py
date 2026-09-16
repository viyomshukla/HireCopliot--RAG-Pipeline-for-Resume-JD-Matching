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

from sqlalchemy import create_engine, event, inspect, text
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

@event.listens_for(engine, "connect")
def _enable_foreign_keys(dbapi_connection, _record) -> None:
    """SQLite ignores foreign keys unless asked, per connection.

    Without this every `ON DELETE CASCADE` in models.py is decoration: deleting
    a candidate leaves its skills, experience and education rows behind. And
    because SQLite reuses the highest freed rowid, the NEXT candidate inserted
    can be given that id and silently inherit a stranger's skills.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# Columns added after the first release. create_all() only creates missing
# TABLES, never missing columns, so an existing database would otherwise fail
# with "no such column" on the first query that names one.
_ADDED_COLUMNS = {
    "candidates": {
        "job_id": "VARCHAR(64)",
        "duplicate_of": "VARCHAR(64)",
    },
}


def _add_missing_columns() -> list[str]:
    added = []
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    added.append(f"{table}.{name}")
    return added


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

    added = _add_missing_columns()
    if "candidates.duplicate_of" in added:
        # A freshly added column is NULL for every existing row, which would
        # read as "no duplicates" rather than "never checked". Compute it once.
        from app.db.duplicates import backfill_duplicates
        with get_session() as session:
            backfill_duplicates(session)


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