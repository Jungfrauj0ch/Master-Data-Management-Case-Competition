"""Shared settings for the MDM application."""

RECORD_TYPES = ("Customer", "Vendor", "Item", "Cost Center")

BUSINESS_ENTITIES = ("SPD", "QTS", "QTG")

# Illustrative locations, not verified Lives Group countries
COUNTRIES = (
    "Singapore",
    "Malaysia",
    "Thailand",
    "Indonesia",
    "Vietnam",
)

DUPLICATE_THRESHOLD = 88
MAX_NAME_LENGTH = 200
MAX_JUSTIFICATION_LENGTH = 2000

APPROVAL_ROLES = {
    "business_owner": "Business owner",
    "data_steward": "Data steward",
    "finance": "Finance",
    "compliance": "Compliance",
    "regional_manager": "Regional manager",
}

# Independent checks run concurrently. Every required check must pass.
APPROVAL_POLICIES = {
    "Customer": tuple(APPROVAL_ROLES),
    "Vendor": tuple(APPROVAL_ROLES),
    "Item": ("business_owner", "data_steward", "regional_manager"),
    "Cost Center": ("business_owner", "finance", "regional_manager"),
}
APPROVAL_SLA_HOURS = 48
REMINDER_INTERVAL_HOURS = 24

# One schema drives both the form and server-side validation.
INTAKE_FIELDS = {
    "Customer": {
        "registration_number": "Registration number",
        "contact_email": "Business contact email",
        "address": "Registered address",
    },
    "Vendor": {
        "registration_number": "Registration number",
        "contact_email": "Business contact email",
        "address": "Registered address",
    },
    "Item": {
        "item_code": "Item code",
        "category": "Item type",
    },
    "Cost Center": {
        "cost_center_code": "Cost center code",
        "department": "Department",
    },
}

# Shared dropdown values are enforced by both the form and request validation.
INTAKE_OPTIONS = {
    "Item": {
        "category": (
            "In-vitro diagnostics (IVD)",
            "Medical equipment",
            "Laboratory solutions",
        ),
    },
}
