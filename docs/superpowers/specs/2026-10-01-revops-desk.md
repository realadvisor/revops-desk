# RevOps Desk

Approved brief: one-minute stakeholder intake, a shared request queue and a focused owner queue. Theo explicitly authorized the RealAdvisor GitHub repository and Vercel production deployment, and requested continuous execution.

One invited team, one RevOps project. Required title, description, topic, country and urgency; optional requested deadline and files. Topics: Mako, Close, Billing, Automations, Booking, Data, RealAdvisor CRM, Other. Countries: France, Italy, Spain, Poland, Portugal, Switzerland, Global/multiple. Categories live in the database and can be maintained by the owner.

States: New, Planned, In progress, Waiting, Done, Cancelled. Requested deadline is distinct from delivery date. Stakeholders can create, read and comment; only managers change status, priority, assignment and delivery date. Full timestamped history; requester gets an unread indicator for subsequent changes. Search, filters, my requests, management queue and board view. Private attachments, invite-only access, account recovery through a fresh manager-generated link.

Reuse Plane's actual Django users, workspaces, projects, issues, labels, states, comments and activity models and migrations. New desk models store only requested dates, attachments, invitation token hashes, login throttling and read receipts. Serve Django templates with native CSS and small progressive JavaScript on Vercel; no separate frontend build or task queue. PostgreSQL is a dedicated Neon database, not any existing business database. Preserve upstream source and AGPL notices.

Use invitation links to activate password accounts, Django sessions and CSRF. An invite is one-use, expires after seven days, and is bound to an email and role. Auth and authorization are enforced on the server. No public signup, ticket access, Slack ingestion or Slack posting. Existing RevOps OS ticket ledger is unchanged.

Success means a deployed URL, RealAdvisor fork, passing integration checks for submission/update/comment/history/file isolation/invite replay/role denial, and desktop/mobile browser verification. No demonstration tickets remain in production after verification.
