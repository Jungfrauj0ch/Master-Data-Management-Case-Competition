# Lives Group Master Data Management

A Streamlit case-competition prototype with standardized intake, duplicate discovery,
parallel approvals, request tracking, request conversations, and an audit trail.
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

## Deploy a public demo

The included `render.yaml` configures a free Render web service in Singapore,
using Python 3.13, the host's assigned port, and Streamlit's health endpoint.

1. Push this project, including `render.yaml` and `.python-version`, to GitHub.
2. In [Render](https://dashboard.render.com/), choose **New > Blueprint**,
   connect this repository, and select the `main` branch.
3. Review the `livesflow-mdm` service and deploy it. When the deployment is live,
   use the service's assigned `https://...onrender.com` URL to share the app.

This deployment is a shared, synthetic-data demo. Anyone with the link can
select any demo identity and use its workflow permissions. The free service
sleeps when idle, and its SQLite database at `/tmp/lives_mdm.db` is temporary:
submitted records can disappear on restart, redeployment, or spin-down. Bundled
synthetic analytics files and the seeded demo master are restored from source.
See [Render's free-service limits](https://render.com/docs/free).

Protected financial fields remain disabled unless you set `MDM_ENCRYPTION_KEY`
in the service's environment to a Fernet key; see **Protected fields** below.
For durable records, use a paid service with a persistent disk, set
`MDM_DATABASE_PATH` to a file within its mount path, and retain the encryption key
across deployments. Configure OIDC before using real data.

## Regional forecasting and sales intelligence

Open **Regional Sales Analytics → Forecasting** for monthly forecasts of Products
A, B and C across the five illustrative countries. The dashboard compares a
six-month average, robust damped trend, damped Holt and ARIMA on matching 1-, 3-,
6- or 12-month test horizons. **Trend outlook** defaults to a robust projection of
recent momentum that eases over time. **Best historical fit** uses the model
selected by historical testing. The default trend view runs only the lightweight
baseline and trend checks; Holt and ARIMA fitting runs when **Best historical fit**
is selected. Each horizon and view is cached for subsequent visits while the app
process stays running. Regional totals sum the underlying forecasts.
The page separates **Outlook & actions** and **Regional priorities**, with a
small **ⓘ** button for a brief model explanation. Recommendation cards identify supply reviews, demand
review frequency, and sales or retention follow-ups, with an owner for each.
Monthly and regional CSV exports include the forecast view and low/high scenarios.
**Sales Insights** ranks missing-product reviews using reconciled identities
and same-country peer adoption, alongside declining-account follow-ups.

The bundled data contains only 12 synthetic months. The trend outlook is a
planning projection; improved long-range accuracy is not established. Best
historical fit retains the baseline when fewer than three matched test origins
are available. Low/high scenarios reflect historical variability and are not
calibrated confidence intervals. No real accuracy, productivity or sales uplift
is claimed. Forecasts follow the dataset cutoff, not the current date. Input
months must be complete; missing orders are treated as zero.

See [the case answer](regional_analytics/CASE_ANSWER.md) for the proposed business
approach, reference-project adaptation, pilot design and success measures.
The implementation is original and draws on the workflow described in
[panyashree12/Sales-Forecasting](https://github.com/panyashree12/Sales-Forecasting).

## Workflow

The Dashboard and Approval Workflow show a compact **You have X notifications**
card with a **Go to Messages** button inside it. The card counts unread updates
and highlights unanswered approver questions. The button opens the relevant
conversation, prioritizing requests that need a reply.
**Reply to request** in a record's details opens that specific conversation.
Every identity has a **Messages** page containing its authorized requests and
conversations. Assigned approvers send questions there;
requestors reply there, and auditors can read conversations within their scope.
**Message requestor** opens a conversation from Approval Workflow, and **Review
request** returns to the approval decision. Dashboard and Approval Workflow show
alerts for unanswered questions. The action alert clears when the questions are
answered or the request is closed. Full notification inboxes and read controls
are not displayed.

Duplicate detection compares records of the same type and country across business
entities, searching master records and pending requests. Similar names (score 88
or higher), matching registration numbers, item codes, or cost center codes block
submission. Identifier comparison ignores case, spacing, and punctuation. Exact
normalized email or address matches produce review warnings without blocking,
because contacts and premises may be shared. Matching reasons are displayed without
revealing the matched field values. Intake fields are checked on submission; the
name preview also uses any form values already submitted. Final approval repeats
the blocking checks against master records. Existing approved requests backfill
their master records' intake details on startup; older standalone masters without
those details can only be matched by name until their details are supplied.

Requestors can check **Delete…** on their own request's row in the Dashboard table
before a master record is created. A dialog identifies the request and explains
the effect. Choose **Delete request** to confirm, or **Cancel** (or close the dialog)
to keep it. Deletion hides the request from active views,
cancels outstanding approvals and notifications, and retains audit history.
Approved requests with master records cannot be deleted.

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
   Open an approved request and click **Prepare simulated SAP export**, then
   **Download simulated SAP JSON** to prepare its handoff file. Preparation is
   audited with a timestamp; it does not confirm download or SAP receipt. The file
   includes a stable handoff reference, local master ID, identity, and standardized
   intake fields. Protected financial fields are excluded. This demo JSON format
   requires mapping to a target SAP interface before real integration.
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
independent checks continue. The requestor sees a reply-needed alert and can
reply from Messages or open the conversation from the Dashboard request row. Only
the original requestor, within their access scope, can reply.

The reply notifies the approver, returns their check to **Pending**, and restarts
its approval deadline. Approver reminders pause while a reply is outstanding.
The approver can approve, reject, or ask a follow-up question. Each question can
be answered once; concurrent or repeated submissions are rejected. Rejecting
the request cancels any checks waiting for clarification and makes unanswered
questions read-only. Questions, replies, authors, and UTC timestamps remain in
the request history and audit export. Keep protected financial values in their
designated encrypted fields, not in clarification messages.

## Deadlines and refresh

The app retains notification records for workflow events and reminders and shows
a compact unread count. Full notification inboxes and read controls are not shown.
Dashboard and Approval Workflow refresh their requests, decisions, due dates,
and question alerts automatically every five seconds while the page is open.
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
