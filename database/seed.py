
from sqlalchemy import select

from database.connection import write_session
from models.mdm import MasterRecord


def seed_demo_records():
    with write_session() as session:
        exists = session.scalar(
            select(MasterRecord.id).where(
                MasterRecord.record_type == "Customer",
                MasterRecord.normalized_name
                == "national university hospital",
                MasterRecord.country == "Singapore",
            ).limit(1)
        )

        if exists is None:
            session.add(
                MasterRecord(
                    record_type="Customer",
                    name="National University Hospital",
                    normalized_name="national university hospital",
                    country="Singapore",
                    business_entity="SPD",
                )
            )
