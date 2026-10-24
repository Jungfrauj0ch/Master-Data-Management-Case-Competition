"""Authorization shared by UI and services. Actors come from trusted authentication."""

from dataclasses import dataclass

from mdm_config import APPROVAL_ROLES, BUSINESS_ENTITIES, COUNTRIES


@dataclass(frozen=True)
class Actor:
    id: str
    roles: frozenset[str]
    countries: tuple[str, ...] = COUNTRIES
    entities: tuple[str, ...] = BUSINESS_ENTITIES
    demo: bool = False


def in_scope(actor: Actor, request) -> bool:
    return request.country in actor.countries and request.business_entity in actor.entities


def can_view(actor: Actor, request) -> bool:
    return in_scope(actor, request) and (
        request.requestor == actor.id
        or bool(actor.roles.intersection({*APPROVAL_ROLES, "auditor"}))
    )


def require_view(actor: Actor, request):
    if request is None or not can_view(actor, request):
        raise PermissionError("Request unavailable or access denied.")


def can_decide(actor: Actor, request, role: str) -> bool:
    return (
        in_scope(actor, request)
        and role in actor.roles
        and request.requestor != actor.id
        and request.status == "Pending Approval"
    )
