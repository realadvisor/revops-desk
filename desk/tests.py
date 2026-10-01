import hashlib
import secrets
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, Client, override_settings
from django.utils import timezone
from plane.db.models import User, Project, ProjectMember, WorkspaceMember, Label, Issue, State
from desk.models import RequestDetails, Invitation, Attachment, ReadReceipt


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class DeskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_desk", email="manager@example.com", verbosity=0)
        cls.project = Project.objects.get(identifier="REV")
        cls.manager = cls.project.project_lead
        cls.person = User.objects.create(username="requester", email="requester@example.com", first_name="Alex")
        cls.person.set_password("A-strong-passphrase-827!")
        cls.person.save()
        WorkspaceMember.objects.create(workspace=cls.project.workspace, member=cls.person, role=5)
        ProjectMember.objects.create(project=cls.project, member=cls.person, role=5)

    def setUp(self):
        self.client.force_login(self.person)

    def payload(self):
        import uuid
        return {"title": "A useful request", "description": "The dashboard needs a country filter.",
                "topic": str(Label.objects.get(project=self.project, name="Mako & dashboards").pk),
                "country": str(Label.objects.get(project=self.project, name="France").pk),
                "priority": "medium", "requested_deadline": "2030-01-01", "submission_key": str(uuid.uuid4())}

    def create_ticket(self):
        data = self.payload()
        response = self.client.post("/requests/new/", data)
        self.assertEqual(response.status_code, 302)
        return Issue.objects.get(name=data["title"])

    def test_submission_history_update_comment_files_and_roles(self):
        ticket = self.create_ticket()
        self.assertEqual(ticket.created_by, self.person)
        self.assertIsNone(ticket.target_date)
        self.assertEqual(str(ticket.request_details.requested_deadline), "2030-01-01")
        url = f"/requests/{ticket.pk}/"
        self.assertContains(self.client.get(url), ticket.name)
        self.assertEqual(self.client.post(url + "manage/", {"status": "Done"}).status_code, 403)
        self.assertEqual(self.client.get("/team/").status_code, 403)
        self.client.force_login(self.manager)
        data = {"state": str(State.objects.get(project=self.project, name="In progress").pk),
                "priority": "high", "assignee": str(self.manager.pk), "target_date": "2030-01-02",
                "version": ticket.updated_at.isoformat()}
        self.assertEqual(self.client.post(url + "manage/", data).status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.state.name, "In progress")
        self.assertEqual(str(ticket.target_date), "2030-01-02")
        self.assertEqual(self.client.post(url + "manage/", data).status_code, 409)
        self.client.force_login(self.person)
        self.assertEqual(self.client.post(url + "comment/", {"comment": "<script>alert(1)</script> Thanks!"}).status_code, 302)
        response = self.client.get(url)
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertEqual(self.client.post(url + "attachment/", {"attachment": SimpleUploadedFile("note.txt", b"Private note")}).status_code, 302)
        file = Attachment.objects.get(issue=ticket)
        self.assertEqual(self.client.get(f"/files/{file.pk}/").status_code, 200)
        anonymous = Client()
        self.assertEqual(anonymous.get(f"/files/{file.pk}/").status_code, 302)
        self.assertEqual(anonymous.get(url).status_code, 302)
        self.assertGreaterEqual(ticket.issue_activity.count(), 4)

    def test_invalid_inputs_and_duplicate_submission(self):
        data = self.payload()
        invalid = {**data, "requested_deadline": "not-a-date"}
        self.assertEqual(self.client.post("/requests/new/", invalid).status_code, 400)
        self.assertEqual(Issue.objects.count(), 0)
        invalid = {**data, "country": data["topic"]}
        self.assertEqual(self.client.post("/requests/new/", invalid).status_code, 400)
        self.assertEqual(Issue.objects.count(), 0)
        invalid = {**data, "attachment": SimpleUploadedFile("large.txt", b"a" * (3 * 1024 * 1024 + 1))}
        self.assertEqual(self.client.post("/requests/new/", invalid).status_code, 400)
        self.assertEqual(Issue.objects.count(), 0)
        self.assertEqual(self.client.post("/requests/new/", data).status_code, 302)
        self.assertEqual(self.client.post("/requests/new/", data).status_code, 302)
        self.assertEqual(Issue.objects.count(), 1)

    def test_invite_one_use_expiry_and_throttle(self):
        self.client.logout()
        token = secrets.token_urlsafe(32)
        invite = Invitation.objects.create(token_hash=hashlib.sha256(token.encode()).hexdigest(),
            email="new@example.com", name="New colleague", project=self.project, role=5,
            created_by=self.manager, expires_at=timezone.now() + timedelta(days=1))
        url = f"/join/{token}/"
        data = {"name": "New colleague", "password": "My-new-strong-password-123!", "confirm_password": "My-new-strong-password-123!"}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertTrue(ProjectMember.objects.filter(project=self.project, member__email="new@example.com", role=5).exists())
        self.client.logout()
        self.assertEqual(self.client.post(url, data).status_code, 410)
        invite.used_at = None
        invite.expires_at = timezone.now() - timedelta(seconds=1)
        invite.save()
        self.assertEqual(self.client.post(url, data).status_code, 410)
        for _ in range(10):
            self.client.post("/login/", {"email": "requester@example.com", "password": "wrong"})
        self.assertEqual(self.client.post("/login/", {"email": "requester@example.com", "password": "A-strong-passphrase-827!"}).status_code, 429)

    def test_nonmember_and_csrf_denied(self):
        outsider = User.objects.create(username="outsider", email="outsider@example.com")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get("/").status_code, 403)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.person)
        self.assertEqual(protected.post("/requests/new/", self.payload()).status_code, 403)

    def test_read_receipt_covers_only_the_loaded_version(self):
        from desk.views import mark_seen
        ticket = self.create_ticket()
        loaded_version = ticket.updated_at
        Issue.objects.filter(pk=ticket.pk).update(updated_at=timezone.now() + timedelta(seconds=1))
        mark_seen(ticket, self.person)
        self.assertEqual(ReadReceipt.objects.get(issue=ticket, user=self.person).seen_at, loaded_version)


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class AccessConcurrencyTests(TransactionTestCase):
    def test_rotation_and_cross_revocation_are_serialized(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import close_old_connections, connections
        from desk.views import make_invitation
        call_command("bootstrap_desk", email="manager@example.com", verbosity=0)
        project = Project.objects.get(identifier="REV")
        owner = project.project_lead

        def invite(_):
            close_old_connections()
            try:
                return make_invitation(project, owner, "new@example.com", "New colleague", 5)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(invite, range(2)))
        self.assertEqual(Invitation.objects.filter(email="new@example.com", used_at__isnull=True).count(), 1)

        second = User.objects.create(username="second", email="second@example.com")
        second_member = ProjectMember.objects.create(project=project, member=second, role=20)
        first_member = ProjectMember.objects.get(project=project, member=owner)
        clients = [Client(), Client()]
        clients[0].force_login(owner)
        clients[1].force_login(second)
        def revoke(args):
            client, target = args
            close_old_connections()
            try:
                return client.post(f"/team/{target.pk}/revoke/").status_code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(revoke, [(clients[0], second_member), (clients[1], first_member)]))
        self.assertEqual(sorted(statuses), [302, 403])
        self.assertEqual(ProjectMember.objects.filter(project=project, role=20, is_active=True).count(), 1)
