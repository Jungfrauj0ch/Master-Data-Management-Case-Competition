"""Unicode-aware duplicate discovery across master data and active intake."""

from dataclasses import dataclass
import json
import re
import unicodedata

from rapidfuzz import fuzz
from sqlalchemy import select

from mdm_config import DUPLICATE_THRESHOLD, INTAKE_FIELDS
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
    reasons: tuple[str, ...] = ()
    blocking: bool = True


def similarity(left: str, right: str) -> float:
    score = fuzz.WRatio(left, right)
    # Common abbreviations, e.g. NUH / National University Hospital.
    for short, long in ((left, right), (right, left)):
        words = long.split()
        if len(words) > 1 and len(short) >= 3 and short == "".join(word[0] for word in words):
            score = max(score, 95)
    return score


def normalize_identity(key: str, value: str) -> str:
    if key == "contact_email":
        return unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = normalize_name(value)
    # Ignore separators in identifiers, but preserve word boundaries in addresses.
    return normalized if key == "address" else normalized.replace(" ", "")


def find_matches(session, record_type: str, name: str, country: str,
                 details: dict[str, str] | None = None) -> tuple[DuplicateMatch, ...]:
    normalized = normalize_name(name)
    fields = INTAKE_FIELDS.get(record_type, {})
    identities = {key: normalize_identity(key, value) for key, value in (details or {}).items()
                  if key in fields and key in {"registration_number", "item_code", "cost_center_code", "contact_email", "address"}}
    identities = {key: value for key, value in identities.items() if value}
    if not normalized and not identities:
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
            score = similarity(normalized, normalize_name(record.name)) if normalized else 0
            reasons = ["Similar name"] if score >= DUPLICATE_THRESHOLD else []
            blocking = bool(reasons)
            stored = json.loads(record.details_json)
            for key, value in identities.items():
                if value == normalize_identity(key, stored.get(key, "")):
                    reasons.append(fields[key])
                    score = 100
                    if key in {"registration_number", "item_code", "cost_center_code"}:
                        blocking = True
            if reasons:
                matches.append(DuplicateMatch(record.id, source, record.name, record.country,
                                              record.business_entity, round(score), tuple(reasons), blocking))
    return tuple(sorted(matches, key=lambda match: (not match.blocking, -match.score, match.source, match.id)))
