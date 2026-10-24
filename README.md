# Lives Group Master Data Management

A Streamlit case-competition prototype with standardized intake, duplicate discovery,
parallel approvals, request tracking, in-app notifications, and an audit trail.
Python 3.11 or newer is required.

## Run

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

The default `MDM_AUTH_MODE=demo` provides a sidebar identity selector. Use synthetic
data only: switching a demo identity is not authentication. The demo seeds
National University Hospital for duplicate-search demonstrations (try `NUH`).
Only demo mode inserts sample data.

## Workflow

1. Select **Requestor**, open **New MDM Request**, and choose a record type.
2. Enter the country and record name. Matching master records and pending
   requests appear automatically when you finish editing the name, or change
   the country or record type. Fill in the remaining required fields and submit.
   Submission repeats duplicate checking in a serialized write transaction.
   Close matches block submission and show the existing identity.
3. Customers and vendors require Business owner, Data steward, Finance,
   Compliance, and Regional manager checks. Items require Business owner,
   Data steward, and Regional manager. Cost centers require Business owner,
   Finance, and Regional manager. These independent checks open together;
   each needs a different person. Policies are configured in `mdm_config.py`.
4. Switch to an approver identity and open **Approval Workflow**. Click a
   request row to open its details, then approve or reject with a reason,
   or send a clarifying question to the requestor. Self-approval, out-of-scope decisions, and repeated
   decisions are rejected in the service layer. Rejection cancels remaining
   checks. All approvals must pass before exactly one local master record is
   created. This prototype does not send data to SAP.
5. Return to **Requestor** and open **Dashboard**. Click a request row to see
   status, completed checks, outstanding roles, due dates, reasons, questions,
   and timestamped audit history. Reply to an approver's question directly in
   the request. Audit
   CSV exports exclude protected values and escape spreadsheet formulas.

Intake rules live in `INTAKE_FIELDS`: customers/vendors require registration,
business contact email, and registered address; items require a code and an
item type selected from In-vitro diagnostics (IVD), Medical equipment, or
Laboratory solutions; cost centers require a code and department. Dropdown
choices live in `INTAKE_OPTIONS`; unit of measure is not collected. These are
illustrative rules to confirm with the business. Bank details and credit terms
are optional, separately protected fields. Do not put sensitive values in names,
addresses, justifications, decision reasons, or access reasons: these ordinary
fields are not encrypted.

## Clarifications

An approver with a pending check can send a question from the request detail.
That check becomes **Needs Clarification** and remains visible in the queue;
independent checks continue. The requestor receives an in-app notification and
can reply from the Dashboard by clicking the corresponding request row. Only
the original requestor, within their access scope, can reply.

The reply notifies the approver, returns their check to **Pending**, and restarts
its approval deadline. Approver reminders pause while a reply is outstanding.
The approver can approve, reject, or ask a follow-up question. Each question can
be answered once; concurrent or repeated submissions are rejected. Rejecting
the request cancels any checks waiting for clarification and makes unanswered
questions read-only. Questions, replies, authors, and UTC timestamps remain in
the request history and audit export. Keep protected financial values in their
designated encrypted fields, not in clarification messages.

## Notifications and deadlines

The app persists notifications immediately when a request is created or a
decision is recorded. Role inboxes respect country/entity scopes, and read
receipts are tracked per user. Requestors receive decision and overdue updates.
Dashboard and Approval Workflow refresh their requests, decisions, due dates,
and notifications automatically every five seconds while the page is open.
The selected request stays selected while it is still available, and decision
drafts are preserved while that approval remains pending. Completed checks
leave the approval queue automatically. No refresh button is required.
Checks become overdue after 48 hours, with at most one reminder per check per
24 hours. Both intervals are configurable in `mdm_config.py`.

For reminders while no browser is open, schedule this command externally
(for example, hourly with Windows Task Scheduler):

```powershell
python -m services.notification_service
```

This command creates in-app notifications; no email, SMS, or chat delivery is
configured. It is safe to rerun: reminder timestamps and notification creation
commit together. The app does not install a background scheduler.

## Protected fields

The `cryptography` dependency and a server-managed Fernet key are required.
Generate a key once and place it in your deployment secret manager. For a local
synthetic-data session, you can generate an ephemeral key without printing it:

```powershell
$env:MDM_ENCRYPTION_KEY = python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep the same key across restarts to read existing protected data. Losing it
makes those values unreadable; changing the key requires a data re-encryption
procedure. Never commit the key. Protected fields are disabled when the key or
dependency is unavailable, and service calls fail without writing a request.

Bank account details and credit terms are encrypted before persistence in a
separate table. Only Finance and Compliance can reveal them, within their
country/entity scopes, after providing an access reason. The access audit must
commit before plaintext is returned. Ordinary tables, notifications, and audit
exports never include those values. Protected inputs are cleared on submission
and identity switches; revealed values are not saved in session state.

## Authenticated mode

Install `Authlib` from `requirements.txt` and configure your organization's
OIDC provider in `.streamlit/secrets.toml`, following the
[Streamlit authentication documentation](https://docs.streamlit.io/develop/concepts/connections/authentication).
An example configuration (replace every placeholder):

```toml
[auth]
redirect_uri = "https://your-host.example/oauth2callback"
cookie_secret = "a-long-random-secret"
client_id = "your-client-id"
client_secret = "your-client-secret"
server_metadata_url = "https://your-provider.example/.well-known/openid-configuration"
```

Set `MDM_AUTH_MODE=oidc`. Authorization comes from the server environment
`MDM_USERS_JSON`, keyed by the exact OIDC `iss` and `sub` joined with `|`:

```json
{
  "https://your-provider.example|requestor-subject": {
    "roles": ["requestor"],
    "countries": ["Singapore"],
    "entities": ["SPD"]
  },
  "https://your-provider.example|finance-subject": {
    "roles": ["finance"],
    "countries": ["Singapore"],
    "entities": ["SPD"]
  }
}
```

Allowed roles: `requestor`, `business_owner`, `data_steward`, `finance`,
`compliance`, `regional_manager`, `auditor`. Unknown accounts and invalid
configuration are denied. Auditors can view requests/history within their
scopes but cannot decide requests or reveal protected fields. Ordinary
requestors see their own submissions. Duplicate discovery intentionally exposes
minimal identity metadata across entities within an authorized country so users
can discover existing shared records; it does not expose request payloads.
Services accept an `Actor` from this trusted authentication boundary; these
Python functions must not be exposed as endpoints accepting client-supplied roles.

## Database and operational limits

SQLite defaults to `database/lives_mdm.db`. Set `MDM_DATABASE_PATH` before startup
to use a different path; its parent directory must already exist. SQLite foreign
keys are enabled, and write transactions serialize duplicate checks and approval
decisions. Existing demo databases receive additive schema changes. Legacy
pending requests are retained as **Needs Update** with an audit entry because
they lack standardized fields and approval assignments; resubmit them through
the new form. Those legacy rows do not block corrected submissions.

This is a local prototype. Audit rows are append-only through the application,
but a database administrator can alter SQLite data. Production deployment needs
HTTPS, managed key storage and rotation, database/backup access controls,
independent audit retention, and confirmed approval policies. The optional
OIDC path needs validation against your configured identity provider. Demo mode
provides no identity security even if encryption is configured.

## Verify

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q -p no:cacheprovider
```

Tests use isolated temporary SQLite files in `tests/` and never modify the demo
database. They cover intake, duplicates, concurrent submissions and decisions,
parallel completion, rejection, permissions, reminder throttling, audit export,
and migration. Encryption tests skip when `cryptography` is unavailable.
