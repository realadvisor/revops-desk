from django.core.management.base import BaseCommand
from django.db import transaction
from plane.db.models import User, Workspace, WorkspaceMember, Project, ProjectMember, State, Label

TOPICS = ["Mako & dashboards", "Close", "Billing & payments", "Automations", "Booking", "Data & reporting", "RealAdvisor CRM", "Other"]
COUNTRIES = ["France", "Italy", "Spain", "Poland", "Portugal", "Switzerland", "Global / multiple"]
STATES = [("New", "backlog", "#737d8b"), ("Planned", "unstarted", "#5271bf"),
          ("In progress", "started", "#dc9633"), ("Waiting", "started", "#9360ac"),
          ("Done", "completed", "#268766"), ("Cancelled", "cancelled", "#969ca6")]


class Command(BaseCommand):
    help = "Idempotently initialize this isolated RevOps Desk instance."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", default="Theo")

    @transaction.atomic
    def handle(self, *args, **options):
        email = options["email"].lower().strip()
        owner, created = User.objects.get_or_create(email=email, defaults={"username": email, "first_name": options["name"], "display_name": options["name"]})
        if created:
            owner.set_unusable_password()
            owner.save()
        workspace, _ = Workspace.objects.get_or_create(slug="realadvisor", defaults={"name": "RealAdvisor", "owner": owner, "timezone": "Europe/Paris"})
        WorkspaceMember.objects.get_or_create(workspace=workspace, member=owner, defaults={"role": 20})
        project, _ = Project.objects.get_or_create(workspace=workspace, identifier="REV", defaults={"name": "RevOps", "project_lead": owner, "default_assignee": owner, "network": 0, "timezone": "Europe/Paris"})
        ProjectMember.objects.get_or_create(project=project, member=owner, defaults={"role": 20})
        for rank, (name, group, color) in enumerate(STATES):
            state, _ = State.objects.get_or_create(project=project, name=name, defaults={"group": group, "color": color, "sequence": rank * 10000, "default": name == "New"})
            if name == "New" and not project.default_state_id:
                project.default_state = state
                project.save()
        for group, names in [("Topic", TOPICS), ("Country", COUNTRIES)]:
            parent, _ = Label.objects.get_or_create(project=project, name=group, defaults={"workspace": workspace})
            for name in names:
                Label.objects.get_or_create(project=project, name=name, defaults={"workspace": workspace, "parent": parent})
        self.stdout.write(self.style.SUCCESS("RevOps workspace, categories and owner ready."))
