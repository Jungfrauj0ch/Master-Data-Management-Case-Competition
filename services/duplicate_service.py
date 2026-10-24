"""Unicode-aware duplicate discovery across master data and active intake."""

from dataclasses import dataclass
import re
import unicodedata

from rapidfuzz import fuzz
from sqlalchemy import select

from mdm_config import DUPLICATE_THRESHOLD
from models.mdm import MasterRecord, MdmRequest


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[^\W_]+", value, flags=re.UNICODE))


@dataclass(frozen=True)
class DuplicateMatch:
    id: int
    source: str
    name: str
    country: str
    entity: str
    score: int


def similarity(left: str, right: str) -> float:
    score = fuzz.WRatio(left, right)
    # Common abbreviations, e.g. NUH / National University Hospital.
    for short, long in ((left, right), (right, left)):
        words = long.split()
        if len(words) > 1 and len(short) >= 3 and short == "".join(word[0] for word in words):
            score = max(score, 95)
    return score


def find_matches(session, record_type: str, name: str, country: str) -> tuple[DuplicateMatch, ...]:
    normalized = normalize_name(name)
    if not normalized:
        return ()
    masters = session.scalars(select(MasterRecord).where(
        MasterRecord.record_type == record_type, MasterRecord.country == country,
    )).all()
    requests = session.scalars(select(MdmRequest).where(
        MdmRequest.record_type == record_type,
        MdmRequest.country == country,
        MdmRequest.status == "Pending Approval",
    )).all()
    matches = []
    for source, records in (("Master", masters), ("Request", requests)):
        for record in records:
            score = similarity(normalized, normalize_name(record.name))
            if score >= DUPLICATE_THRESHOLD:
                matches.append(DuplicateMatch(record.id, source, record.name, record.country,
                                              record.business_entity, round(score)))
    return tuple(sorted(matches, key=lambda match: (-match.score, match.source, match.id)))
