# Ask Ops

RealAdvisor's internal ops help desk (automations, dashboard fixes, CRM and billing changes): a lightweight request desk for everyone in RealAdvisor Slack built on [Plane](https://github.com/makeplane/plane).

**Live:** https://revops-desk.vercel.app · **Source:** https://github.com/realadvisor/revops-desk

Stakeholders submit a topic, country, urgency and optional requested deadline, then follow status and discussion. Managers triage one shared queue, assign an owner, set a delivery date and manage access. List and board views include search, filters and unread indicators. All desk members can read all requests; do not put restricted HR or personal records in the shared queue.

## Owner and team access

RealAdvisor Slack members join automatically as requesters on their first `/askops` command. No invitation or password is needed. **My requests** in the Slack form and **Open ticket** after submission provide private, one-use browser sign-in links (15-minute expiry). Opening a link shows a confirmation button; pressing it signs in for the normal 30-day session. Run `/askops` again for a fresh link.

Existing manager roles are preserved. Guests, external/deactivated Slack users and manually removed Desk accounts cannot join automatically. Team & access lists members and lets managers remove access. Legacy activation/password login remains available for owner recovery, but it is not part of stakeholder onboarding.

## Submit from Slack

The Slack integration supports `/askops` and **Create Ask Ops ticket** from a message menu or Slack's shortcuts. It uses the same request form validation and shared queue, with a private confirmation and ticket link. The Slack form accepts up to three attachments (PNG, JPG, PDF, CSV or TXT; 3 MB each), saved privately on the ticket. This requires approving the dedicated app's additional `files:read` scope before deploying the attachment-enabled form. See [Slack setup](integrations/slack/README.md) for the app manifest, required permissions and deployment configuration. The dedicated app is installed in RealAdvisor Slack and configured for production.

Requesters get a private Slack message from the app when their ticket's status or delivery date changes or someone else comments on it. Image attachments are previewed on the request page; PDF, CSV and TXT files open in a browser tab.

## Run locally

Use Python 3.12 and an isolated PostgreSQL database. Never point development or tests at another application's database.

```sh
python3.12 -m venv .venv-desk
source .venv-desk/bin/activate
pip install -r requirements.txt
```

Create an ignored `.env.local` containing `DATABASE_URL`, a randomly generated `SECRET_KEY`, and `DESK_DEBUG=1`. Then:

```sh
python manage.py migrate
python manage.py bootstrap_desk --email owner@example.com --name Owner
python manage.py runserver 127.0.0.1:4783
```

Bootstrap is idempotent and does not set a password. For the initial activation, run `python manage.py shell` and create a private invitation:

```python
from plane.db.models import Project, User
from desk.views import make_invitation
project = Project.objects.get(identifier="REV", workspace__slug="realadvisor")
owner = User.objects.get(email="owner@example.com")
token = make_invitation(project, owner, email=owner.email, name=owner.first_name, role=20)
print("http://127.0.0.1:4783/join/" + token + "/")
```

Do not commit or publicly share activation links, credentials or database URLs.

## Checks

```sh
python manage.py check
python manage.py test desk --keepdb --noinput
```

The test runner creates a separate `test_` database and needs permission to create it. Integration checks cover ticket creation, attachments, escaped discussion, manager-only updates, stale edits, validation and idempotency, session/CSRF boundaries, invitation replay/expiry, login throttling, read receipts and concurrent access changes.

## Deployment

Vercel project `realadvisor/revops-desk`, production branch `revops`, Frankfurt region. Root `vercel.json` and `pyproject.toml` configure Django. Neon supplies isolated production (`neondb`) and preview (`revops_preview`) databases; Django reads only `DATABASE_URL`. Production and preview must keep different database URLs. GitHub Actions inherited from Plane are disabled on this fork.

Required environment variables: `DATABASE_URL`, `SECRET_KEY`, `ALLOWED_HOSTS`, `DJANGO_SETTINGS_MODULE=desk.settings`. Never enable `DESK_DEBUG` on Vercel. Run migrations against the correct environment before deploying schema changes; builds deliberately do not mutate databases. Deploy with `vercel --prod --scope realadvisor`. Back up the database before destructive migrations; a Vercel code rollback does not revert data.

## Maintenance and upstream

Plane's Django users, memberships, projects, issues, states, labels, comments, activities and migrations remain the underlying model. `desk/` adds a small server-rendered interface and request metadata. No Redis, worker or separate frontend service is required. Private attachments are stored in PostgreSQL, limited to 3 MiB each and 20 per request; use object storage if volume grows. Topics and countries are editable through Settings.

The upstream source is retained. The only upstream runtime change is a conditional Celery initialization guard in `apps/api/plane/__init__.py`; the `REVOPS_DESK` entrypoints avoid launching Plane's worker integration. This fork retains Plane's AGPL-3.0 license; see [LICENSE](LICENSE).
