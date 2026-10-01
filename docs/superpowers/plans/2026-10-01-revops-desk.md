# RevOps Desk Implementation Plan

> Execute inline using superpowers:executing-plans. The user authorized continuous execution and deployment; do not add approval handoffs.

**Goal:** Ship a working, private RevOps request desk on Vercel.
**Architecture:** A small Django app on Plane's models with isolated Neon PostgreSQL.
**Tech stack:** Python, Django, PostgreSQL, native HTML/CSS/JavaScript.
**Spec:** ../specs/2026-10-01-revops-desk.md

## Global constraints
- Preserve upstream code and licensing. Do not use shared production databases or credentials.
- Every ticket route requires an active project membership; management requires the admin role.
- Plain-text user content stays escaped; files are authenticated downloads.
- Categories are database-backed; requested and committed dates are separate.

## Review focus
- Direct POST attempts by stakeholders must not change management fields.
- Expired or used invitations must never establish sessions.
- Concurrent edits must detect stale versions instead of silently overwriting.
- Invalid dates, external category IDs and oversized files must leave no partial ticket.
- Unauthenticated HTML and file routes must never reveal ticket content.

## Tasks
- [x] 1. Runtime and data: root manage.py, requirements.txt, desk/settings.py, desk/models.py, desk/migrations, desk/management/commands/bootstrap_desk.py. Reuse plane.db; add only desk metadata and access support. Run Django check, migration and bootstrap twice to prove idempotency.
- [x] 2. Behaviors: write desk/tests.py before desk/forms.py and desk/views.py. Test create → manager update → comment → attachment → read receipt using Django TestCase on a isolated test database. Test forbidden mutation, bad category/date, stale update, invite replay and authentication. Run `python manage.py test desk --keepdb` and observe failing then passing assertions.
- [x] 3. Interface: desk/templates/desk and desk/static/desk. Implement accessible form, searchable list, board, detail timeline, team invitations and login. Validate request error preservation, empty states and mobile layouts in the browser.
- [ ] 4. Delivery: Vercel Django configuration, dedicated database and generated secrets. Commit and push the ready fork. Deploy, verify authenticated end-to-end workflow and anonymous denials, remove verification records, document URL and owner activation link locally.

## Execution ledger
- Fork created in realadvisor/revops-desk. Dedicated checkout /Users/realadvisor/dev/revops-desk, branch revops. Upstream GitHub Actions disabled on this new fork to prevent unrelated workflows.
- Ruling: use server-rendered Django instead of the full React/worker stack; preserves Plane data models and reduces hosting and maintenance to one application and one database.
- Ruling: initial authentication uses secure invitations and passwords; avoids repurposing unrelated OAuth clients. Invitations can be shared by the owner, no messages are sent automatically.

- Verified six database integration tests, including concurrent invitation/revocation races. Review findings fixed: serialize project access changes, recheck manager access inside the lock, and record the rendered issue version for read receipts.
- Production is live on revops-desk.vercel.app. Real browser checks covered desktop/mobile, manager updates and requester submission/discussion. Dedicated preview database migrated and bound separately.
