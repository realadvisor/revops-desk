# Slack intake

## Send a message to BB

Open a message's **More actions** menu and select **Send to BB** under Ask Ops.
A private form opens with an optional **Additional prompt** field (3000 characters).
Add instructions, such as "Analyze only; do not change records", then press
**Send to BB**. Leave the field empty to use the usual analysis-and-fix task.
Cancel closes the form without starting work. Submission queues one agent in
the BB RevOps project. The agent follows your prompt and reads the
selected message and its thread, investigates, applies supported fixes, and
reports in that same Slack thread. A private modal confirms queue acceptance.
Queue acceptance does not mean the agent finished. BB must be running with
valid Google credentials. Accepted events wait in Pub/Sub for up to seven days.

The initial release permits only Theo's Slack user ID. Other users receive a
private access notice. The server verifies Slack's signature, workspace, app,
and timestamp before checking this user ID. There are no added Slack scopes.
The BB worker uses the connected Slack account to read the full thread.

Production-only configuration:

- `REVOPS_BB_SLACK_USER_ID`: Theo's verified Slack user ID.
- `REVOPS_BB_WEBHOOK_TOKEN`: the token for BB source `slack-revops-message`.

Create that source for project `proj_q2sxujq58a`, environment `env_5r6a8bat33`,
with [bb-worker-prompt.txt](bb-worker-prompt.txt). Leave `targetThreadId` unset
so each distinct message creates a new thread. Retrieve the source token with
`bb webhooks credentials slack-revops-message --json` into a private file.
Do not expose it in logs or commit it. Configure these variables only in
production, deploy the handler, then add the message shortcut from the manifest.

The idempotency key is workspace, channel, selected-message timestamp and,
for a nonempty prompt, its SHA-256 hash. Repeating the same message and prompt
does not create more agents. The event
contains stable message identifiers and, when provided, `additional_prompt`.
A different prompt starts a new request for the same message. It does not
update the existing agent. Blank prompts retain the original message-only key. Selecting a reply preserves its original parent timestamp.
If delivery is uncertain, the form keeps your prompt. Submit it again to retry.
Modal context is signed, user-bound, and valid for one hour. The handler checks
access again on submission.
Inspect `bb webhooks deliveries` for the resulting BB thread.

To disable new dispatches, remove the Slack shortcut or clear
`REVOPS_BB_SLACK_USER_ID` and redeploy. Pausing the BB source holds already
accepted events rather than discarding them.

## Create a request

Use `/askops [short description]`, the **Create Ask Ops ticket** global shortcut, or the same shortcut from a message's menu. The Slack modal collects topic, country, urgency and an optional requested date, and up to three attachments (PNG, JPG, PDF, CSV or TXT; 3 MB each). Submission creates the same Plane issue as the web form and returns a private confirmation with its link. Selected message text is prefilled for review; only the text the user submits is copied. A request is visible only to its requester and the managers.

A dedicated **Ask Ops** Slack app uses `manifest.json`. It has no message-history scopes, channel notifications or automatic DM ingestion; its only event subscription is `app_home_opened`, which carries no message content. `commands` enables commands/shortcuts; `users:read` and `users:read.email` verify a full workspace member and automatically create requester access on first use. Guests, external users, deactivated users and revoked Desk memberships are rejected. `files:read` enables Slack's native upload field. Only files explicitly selected in that field are downloaded, validated and copied into the ticket's private attachment storage. Message-shortcut files are not silently copied; upload the desired files in the form.

No invitations or passwords are needed. **My requests** in the form and **Open ticket** after submission return a private one-use sign-in link, valid for 15 minutes. GET only shows a landing page (safe for link scanners); a CSRF-protected POST consumes the hashed token and starts a normal Django session. Existing manager roles are preserved, and removed accounts stay blocked. Run `/askops` again for a fresh link.

## Home tab

Clicking **Ask Ops** in the Slack sidebar opens the app's Home tab: a **New request** button that opens the same form, the viewer's own open requests with status and delivery date, and a one-use link to the browser. It is rebuilt each time the tab is opened (`app_home_opened`) and right after the viewer submits a request. Events arrive on the same signed endpoint as commands; Slack's `url_verification` check is answered only after the signature is verified.

## Private updates

The requester gets a private message from the app when their ticket's status changes, when a delivery date is set or moved, and when someone else comments. Nobody is messaged about their own change, priority and owner changes stay in the app, and owners are never messaged. `chat:write` sends the message and `users.lookupByEmail` (already covered by `users:read.email`) finds the requester; the Messages tab is read-only because the app reads no replies. Messages are sent after the save, and a Slack failure (including a missing `chat:write` scope) is logged and never blocks it. The message links to the ticket; a signed-out requester is sent to the sign-in page and comes back in with `/askops`.

Downloads run in parallel with a short timeout to fit Slack's acknowledgement window. Unsupported, oversized or unavailable files keep the form open with an error and create no partial ticket. Submission retries return the existing ticket without downloading or attaching files twice.

The app icon is `askops-icon.png` (source `askops-icon.svg`): the RealAdvisor compass needle in a speech bubble. Manifests cannot carry icons; upload it under Basic Information > Display Information.

## Connect the app

1. Create an app from `manifest.json` in the RealAdvisor workspace; install it after reviewing the five scopes.
2. Set production-only Vercel environment variables `REVOPS_SLACK_SIGNING_SECRET`, `REVOPS_SLACK_BOT_TOKEN`, `REVOPS_SLACK_TEAM_ID`, `REVOPS_SLACK_APP_ID`. Copy these from that dedicated app's Basic Information and OAuth pages. Never reuse RealBot credentials or bind production Slack credentials to preview.
3. When upgrading an existing installation, paste the current `manifest.json` into the app's manifest editor and reinstall the app in Slack to approve new scopes (this also switches the command from `/revops` to `/askops`; the server accepts both) (`files:read` **before deploying the file-input UI**; `chat:write` for private updates, which are skipped until it is granted). Keep the same dedicated app and verify its token after reinstalling.
4. Redeploy and try `/askops`. Verify the modal opens, a real test ticket appears with the correct requester and a repeated submission does not create a duplicate. Remove only the test ticket afterward.

The endpoint returns 503 until all four values are configured. Slack requests require an HMAC over the exact body, a timestamp within five minutes, the configured team ID and, when the payload carries one, the app ID (shortcut payloads do not; the per-app signing secret already proves the app). Refused requests are logged with the reason. Modal metadata is signed, user-bound and expires after one hour. Access is rechecked before submission. The existing UUID submission key and project lock prevent duplicates on retries. Topics/countries are read from the maintained database; Slack supports at most 100 options per select.

Run `python manage.py test desk --keepdb --noinput` with the local isolated preview database configuration. Coverage includes signatures, wrong workspace/app, automatic onboarding, passwordless access/expiry/replay/CSRF, user matching, request validation, identity tampering, revoked access and duplicate delivery.

References: [Slack signatures](https://docs.slack.dev/authentication/verifying-requests-from-slack/), [modals and their three-second response window](https://docs.slack.dev/surfaces/modals/), [app manifests](https://docs.slack.dev/app-manifests/configuring-apps-with-app-manifests/).
