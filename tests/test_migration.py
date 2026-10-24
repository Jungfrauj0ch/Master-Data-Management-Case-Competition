from sqlalchemy import text

from database.connection import initialize_database
from models.mdm import Base
from services.access_service import Actor
from services.request_service import get_request


def test_original_database_is_preserved_and_flagged_for_resubmission(database):
    Base.metadata.drop_all(database)
    with database.begin() as connection:
        connection.execute(text("""
            CREATE TABLE master_records (
                id INTEGER PRIMARY KEY, record_type VARCHAR(30) NOT NULL,
                name VARCHAR(200) NOT NULL, normalized_name VARCHAR(200) NOT NULL,
                country VARCHAR(60) NOT NULL, business_entity VARCHAR(10) NOT NULL
            )
        """))
        connection.execute(text("""
            CREATE TABLE mdm_requests (
                id INTEGER PRIMARY KEY, record_type VARCHAR(30) NOT NULL,
                name VARCHAR(200) NOT NULL, normalized_name VARCHAR(200) NOT NULL,
                country VARCHAR(60) NOT NULL, business_entity VARCHAR(10) NOT NULL,
                justification TEXT NOT NULL, status VARCHAR(30) NOT NULL,
                created_at DATETIME NOT NULL
            )
        """))
        connection.execute(text("""
            CREATE TABLE audit_logs (
                id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES mdm_requests(id),
                action VARCHAR(60) NOT NULL, actor VARCHAR(100) NOT NULL,
                created_at DATETIME NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO mdm_requests VALUES
            (1, 'Vendor', 'Legacy Supplier', 'legacy supplier', 'Singapore', 'SPD',
             'Original submission', 'Pending Approval', '2026-10-01 01:00:00')
        """))
        connection.execute(text("""
            INSERT INTO audit_logs VALUES (1, 1, 'REQUEST_CREATED', 'demo_requestor', '2026-10-01 01:00:00')
        """))
    initialize_database()
    initialize_database()
    request = get_request(1, Actor("demo_requestor", frozenset({"requestor"})))
    assert request["status"] == "Needs Update"
    assert request["justification"] == "Original submission"
    assert len(request["audit"]) == 2
    assert request["audit"][0]["Action"] == "REQUEST_CREATED"
    assert request["audit"][1]["Action"] == "LEGACY_REVIEW_REQUIRED"
