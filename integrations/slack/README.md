# Slack intake

Use `/revops [short description]`, the **Create RevOps ticket** global shortcut, or the same shortcut from a message's menu. The Slack modal collects topic, country, urgency and an optional requested date. Submission creates the same Plane issue as the web form and returns a private confirmation with its link. Selected message text is prefilled for review; only the text the user submits is copied. Submitted content is visible to every member of the desk.

A dedicated **RevOps Desk** Slack app uses `manifest.json`. It has no message-history scopes, event subscriptions, channel notifications or automatic DM ingestion. `commands` enables commands/shortcuts; `users:read` and `users:read.email` verify a full workspace member and automatically create requester access on first use. Guests, external users, deactivated users and revoked Desk memberships are rejected.

No invitations or passwords are needed. **My requests** in the form and **Open ticket** after submission return a private one-use sign-in link, valid for 15 minutes. GET only shows a landing page (safe for link scanners); a CSRF-protected POST consumes the hashed token and starts a normal Django session. Existing manager roles are preserved, and removed accounts stay blocked. Run `/revops` again for a fresh link.

## Connect the app

1. Create an app from `manifest.json` in the RealAdvisor workspace; install it after reviewing the three scopes.
2. Set production-only Vercel environment variables `REVOPS_SLACK_SIGNING_SECRET`, `REVOPS_SLACK_BOT_TOKEN`, `REVOPS_SLACK_TEAM_ID`, `REVOPS_SLACK_APP_ID`. Copy these from that dedicated app's Basic Information and OAuth pages. Never reuse RealBot credentials or bind production Slack credentials to preview.
3. Redeploy and try `/revops`. Verify the modal opens, a real test ticket appears with the correct requester and a repeated submission does not create a duplicate. Remove only the test ticket afterward.

The endpoint returns 503 until all four values are configured. Slack requests require an HMAC over the exact body, a timestamp within five minutes, and the configured team and app IDs. Modal metadata is signed, user-bound and expires after one hour. Access is rechecked before submission. The existing UUID submission key and project lock prevent duplicates on retries. Topics/countries are read from the maintained database; Slack supports at most 100 options per select.

Run `python manage.py test desk --keepdb --noinput` with the local isolated preview database configuration. Coverage includes signatures, wrong workspace/app, automatic onboarding, passwordless access/expiry/replay/CSRF, user matching, request validation, identity tampering, revoked access and duplicate delivery.

References: [Slack signatures](https://docs.slack.dev/authentication/verifying-requests-from-slack/), [modals and their three-second response window](https://docs.slack.dev/surfaces/modals/), [app manifests](https://docs.slack.dev/app-manifests/configuring-apps-with-app-manifests/).
