
from pathlib import Path
from contextlib import contextmanager
import os

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_FILE = Path(os.environ.get("MDM_DATABASE_PATH", Path(__file__).resolve().parent / "lives_mdm.db"))

engine = create_engine(
    f"sqlite:///{DATABASE_FILE.as_posix()}",
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def enable_foreign_keys(connection, _):
    connection.execute("PRAGMA foreign_keys=ON")

SessionLocal = sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


def initialize_database():
    from models import mdm

    Base.metadata.create_all(engine)
    # Additive migration for the original demo database; existing rows are retained.
    with engine.begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        columns = {column["name"] for column in inspect(connection).get_columns("mdm_requests")}
        legacy_schema = "requestor" not in columns
        additions = {
            "requestor": "VARCHAR(200) NOT NULL DEFAULT 'demo_requestor'",
            "details_json": "TEXT NOT NULL DEFAULT '{}'",
            "master_record_id": "INTEGER REFERENCES master_records(id)",
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.exec_driver_sql(f"ALTER TABLE mdm_requests ADD COLUMN {name} {definition}")
        audit_columns = {column["name"] for column in inspect(connection).get_columns("audit_logs")}
        if "details" not in audit_columns:
            connection.exec_driver_sql("ALTER TABLE audit_logs ADD COLUMN details TEXT NOT NULL DEFAULT ''")
        if legacy_schema:
            connection.exec_driver_sql(
                "INSERT INTO audit_logs (request_id, action, actor, created_at, details) "
                "SELECT id, 'LEGACY_REVIEW_REQUIRED', 'system', CURRENT_TIMESTAMP, "
                "'Resubmit using the standardized intake form.' FROM mdm_requests "
                "WHERE status = 'Pending Approval'"
            )
            connection.exec_driver_sql(
                "UPDATE mdm_requests SET status = 'Needs Update' WHERE status = 'Pending Approval'"
            )


@contextmanager
def write_session():
    """Serialize SQLite writes, including the read/check/write part of decisions."""
    with SessionLocal() as session:
        try:
            session.execute(text("BEGIN IMMEDIATE"))
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
