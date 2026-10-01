import hashlib
import io
import os
import secrets
from datetime import timedelta
from functools import wraps
from pathlib import PurePath

from django.contrib import messages
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, When, Value, IntegerField, Exists, OuterRef, Q
from django.db.models.functions import Coalesce
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.http import require_POST, require_http_methods

from plane.db.models import (User, Project, ProjectMember, WorkspaceMember, Label, Issue, IssueLabel,
    IssueAssignee, IssueActivity, IssueComment, State)
from desk.models import RequestDetails, Attachment, Invitation, ReadReceipt, LoginAttempt, SlackLogin
from desk.forms import RequestForm, ManagementForm, CommentForm, AttachmentForm, ActivateForm


def membership(request):
    if not hasattr(request, "desk_member"):
        request.desk_member = (ProjectMember.objects.select_related("project__workspace", "project__project_lead")
            .filter(member=request.user, is_active=True, project__identifier="REV", project__workspace__slug="realadvisor").first()) if request.user.is_authenticated else None
    return request.desk_member


def team_required(view=None, *, manager=False):
    def decorator(func):
        @login_required
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            member = membership(request)
            if not member or (manager and member.role != 20):
                raise PermissionDenied
            request.project = member.project
            return func(request, *args, **kwargs)
        return wrapper
    return decorator(view) if view else decorator


def navigation(request):
    member = membership(request)
    return {"is_manager": bool(member and member.role == 20), "desk_member": member,
        "slack_url": "https://app.slack.com/client/" + settings.SLACK_TEAM_ID if settings.SLACK_TEAM_ID else None}


def history(issue, actor, text, field=None, old=None, new=None):
    IssueActivity.objects.create(issue=issue, project=issue.project, actor=actor, verb="updated" if field else "created",
        field=field, old_value=old, new_value=new, comment=text)


def notify_requester(request, issue, text):
    from desk.slack import notify  # desk.slack imports this module.
    notify(issue, request.user, text, request.build_absolute_uri(reverse("detail", args=[issue.pk])))


def save_attachment(issue, actor, file):
    attachment = Attachment.objects.create(issue=issue, name=PurePath(file.name).name[:180], data=file.read(), size=file.size, uploaded_by=actor)
    history(issue, actor, f"Attached {attachment.name}", "attachment")


def mark_seen(issue, user):
    ReadReceipt.objects.update_or_create(issue=issue, user=user, defaults={"seen_at": issue.updated_at})


def visible_issues(request):
    """Managers see every request; everyone else only their own."""
    issues = Issue.objects.filter(project=request.project, request_details__isnull=False)
    return issues if membership(request).role == 20 else issues.filter(created_by=request.user)


def issue_queryset(request):
    return (visible_issues(request)
        .select_related("state", "created_by", "request_details__topic", "request_details__country", "project")
        .prefetch_related("assignees").annotate(due=Coalesce("target_date", "request_details__requested_deadline")))


@team_required
def queue(request):
    qs = issue_queryset(request)
    open_qs = qs.exclude(state__group__in=["completed", "cancelled"])
    counts = {"all": open_qs.count(), "new": qs.filter(state__name="New").count(),
        "active": qs.filter(state__name="In progress").count(), "waiting": qs.filter(state__name="Waiting").count(),
        "overdue": open_qs.filter(due__lt=timezone.localdate()).count(),
        "mine": open_qs.filter(created_by=request.user).count()}
    selected = request.GET.get("view", "all")
    titles = {"all": "All requests" if membership(request).role == 20 else "My requests", "mine": "My requests", "new": "New requests", "active": "In progress", "waiting": "Waiting", "overdue": "Overdue", "done": "Completed"}
    if selected == "mine":
        qs = open_qs.filter(created_by=request.user)
    elif selected in {"new", "active", "waiting"}:
        qs = qs.filter(state__name={"new": "New", "active": "In progress", "waiting": "Waiting"}[selected])
    elif selected == "overdue":
        qs = open_qs.filter(due__lt=timezone.localdate())
    elif selected == "done":
        qs = qs.filter(state__group__in=["completed", "cancelled"])
    else:
        selected = "all"
        qs = open_qs
    search = request.GET.get("q", "").strip()[:200]
    if search:
        condition = Q(name__icontains=search) | Q(description_stripped__icontains=search) | Q(created_by__first_name__icontains=search)
        number = search.upper().removeprefix("REV-")
        if number.isdigit() and len(number) < 10:
            condition |= Q(sequence_id=int(number))
        qs = qs.filter(condition)
    for field in ("topic", "country"):
        value = request.GET.get(field, "")
        if value:
            qs = qs.filter(**{f"request_details__{field}__name": value})
    status = request.GET.get("status", "")
    if status:
        qs = qs.filter(state__name=status)
    priority = request.GET.get("priority", "")
    if priority:
        qs = qs.filter(priority=priority)
    qs = qs.annotate(seen=Exists(ReadReceipt.objects.filter(issue=OuterRef("pk"), user=request.user, seen_at__gte=OuterRef("updated_at"))),
        urgency_order=Case(When(priority="urgent", then=Value(0)), When(priority="high", then=Value(1)), When(priority="medium", then=Value(2)), default=Value(3), output_field=IntegerField()))
    sort = request.GET.get("sort", "priority")
    qs = qs.order_by(*({"recent": ["-updated_at", "-pk"], "oldest": ["created_at", "pk"], "deadline": ["due", "-created_at"]}.get(sort, ["urgency_order", "due", "-created_at"])))
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    states = list(State.objects.filter(project=request.project))
    layout = "board" if request.GET.get("layout") == "board" else "list"
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "desk/queue.html", {"page": page, "counts": counts, "selected": selected, "title": titles[selected],
        "topics": Label.objects.filter(project=request.project, parent__name="Topic").order_by("sort_order"),
        "countries": Label.objects.filter(project=request.project, parent__name="Country").order_by("sort_order"),
        "states": states, "layout": layout, "search": search, "today": timezone.localdate(), "params": params.urlencode(),
        "columns": [(state, [issue for issue in page if issue.state_id == state.pk]) for state in states] if layout == "board" else []})


@transaction.atomic
def create_request(project, actor, data):
    """One validated submission path for the web form and Slack."""
    Project.objects.select_for_update().get(pk=project.pk)
    if not actor.is_active or not ProjectMember.objects.filter(project=project, member=actor, is_active=True).exists():
        raise PermissionDenied
    existing = RequestDetails.objects.filter(submission_key=data["submission_key"]).select_related("issue").first()
    if existing:
        if existing.issue.created_by_id != actor.pk or existing.issue.project_id != project.pk:
            raise ValueError("Invalid submission reference")
        return existing.issue
    issue = Issue(project=project, name=data["title"], description_html=f"<p>{escape(data['description'])}</p>",
        priority=data["priority"], state=project.default_state)
    issue.save(created_by_id=actor.pk)
    RequestDetails.objects.create(issue=issue, topic=data["topic"], country=data["country"],
        requested_deadline=data["requested_deadline"], submission_key=data["submission_key"])
    for label in (data["topic"], data["country"]):
        IssueLabel.objects.create(project=project, issue=issue, label=label)
    if project.default_assignee_id:
        IssueAssignee.objects.create(project=project, issue=issue, assignee=project.default_assignee)
    history(issue, actor, "Submitted this request")
    if data.get("attachment"):
        save_attachment(issue, actor, data["attachment"])
    for file in data.get("slack_attachments", []):
        save_attachment(issue, actor, file)
    mark_seen(issue, actor)
    return issue


@team_required
def new_request(request):
    form = RequestForm(request.POST or None, request.FILES or None, project=request.project)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            issue = create_request(request.project, request.user, data)
        except ValueError:
            return HttpResponse("Invalid submission reference.", status=400)
        messages.success(request, f"Request REV-{issue.sequence_id} submitted. You can follow every update here.")
        return redirect("detail", pk=issue.pk)
    return render(request, "desk/new.html", {"form": form}, status=400 if request.method == "POST" else 200)


def detail_response(request, issue, *, management_form=None, comment_form=None, attachment_form=None, status=200):
    manager_form = management_form or ManagementForm(project=request.project, initial={"state": issue.state_id,
        "priority": issue.priority, "assignee": issue.assignees.first(), "target_date": issue.target_date,
        "version": issue.updated_at.isoformat()})
    timeline = list(issue.issue_activity.select_related("actor").order_by("created_at"))
    for activity in timeline:
        activity.kind = "activity"
    for comment in issue.issue_comments.select_related("actor").order_by("created_at"):
        comment.kind = "comment"
        timeline.append(comment)
    timeline.sort(key=lambda item: item.created_at)
    return render(request, "desk/detail.html", {"issue": issue, "management_form": manager_form,
        "comment_form": comment_form or CommentForm(), "attachment_form": attachment_form or AttachmentForm(),
        "timeline": timeline, "attachments": issue.desk_attachments.defer("data"), "today": timezone.localdate()}, status=status)


@team_required
def detail(request, pk):
    issue = get_object_or_404(issue_queryset(request), pk=pk)
    mark_seen(issue, request.user)
    return detail_response(request, issue)


@require_POST
@team_required(manager=True)
def manage_request(request, pk):
    with transaction.atomic():
        issue = get_object_or_404(visible_issues(request).select_for_update(), pk=pk)
        form = ManagementForm(request.POST, project=request.project)
        if not form.is_valid():
            return detail_response(request, issue, management_form=form, status=400)
        if form.cleaned_data["version"] != issue.updated_at.isoformat():
            messages.error(request, "This request changed while you were editing. Review the latest values below and save again.")
            return detail_response(request, issue, status=409)
        data = form.cleaned_data
        updates = []
        for field, name in [("state", "Status"), ("priority", "Priority"), ("target_date", "Delivery date")]:
            old, new = getattr(issue, field), data[field]
            if old != new:
                before = old.name if field == "state" else str(old or "Not set")
                after = new.name if field == "state" else str(new or "Not set")
                history(issue, request.user, f"{name}: {before} → {after}", field, before, after)
                setattr(issue, field, new)
                if field != "priority":
                    updates.append(f"{name}: {before} → {after}")
        old_assignee = issue.assignees.first()
        if old_assignee != data["assignee"]:
            issue.issue_assignee.all().delete(soft=False)
            if data["assignee"]:
                IssueAssignee.objects.create(project=request.project, issue=issue, assignee=data["assignee"])
            before = old_assignee.first_name or old_assignee.email if old_assignee else "Unassigned"
            after = data["assignee"].first_name or data["assignee"].email if data["assignee"] else "Unassigned"
            history(issue, request.user, f"Owner: {before} → {after}", "assignee", before, after)
        issue.save()
        mark_seen(issue, request.user)
    if updates:
        notify_requester(request, issue, "\n".join(updates))
    messages.success(request, "Request updated.")
    return redirect("detail", pk=pk)


@require_POST
@team_required
def add_comment(request, pk):
    with transaction.atomic():
        issue = get_object_or_404(visible_issues(request).select_for_update(), pk=pk)
        form = CommentForm(request.POST)
        if not form.is_valid():
            return detail_response(request, issue, comment_form=form, status=400)
        IssueComment.objects.create(project=request.project, issue=issue, actor=request.user, comment_html=f"<p>{escape(form.cleaned_data['comment'])}</p>")
        issue.save()
        mark_seen(issue, request.user)
    notify_requester(request, issue, f"{request.user.first_name or request.user.email} replied: {form.cleaned_data['comment'][:500]}")
    return redirect("detail", pk=pk)


@require_POST
@team_required
def add_attachment(request, pk):
    with transaction.atomic():
        issue = get_object_or_404(visible_issues(request).select_for_update(), pk=pk)
        form = AttachmentForm(request.POST, request.FILES)
        if not form.is_valid() or not form.cleaned_data.get("attachment"):
            if not form.errors:
                form.add_error("attachment", "Choose a file first.")
            return detail_response(request, issue, attachment_form=form, status=400)
        if issue.desk_attachments.count() >= 20:
            form.add_error("attachment", "This request already has 20 attachments. Share a document link in a comment instead.")
            return detail_response(request, issue, attachment_form=form, status=400)
        save_attachment(issue, request.user, form.cleaned_data["attachment"])
        issue.save()
        mark_seen(issue, request.user)
    return redirect("detail", pk=pk)


@team_required
def download(request, pk):
    file = get_object_or_404(Attachment, pk=pk, issue__in=visible_issues(request))
    # Previews only ever get a fixed type chosen from the extension, with nosniff, so uploads cannot run as HTML.
    inline = file.inline_type if "inline" in request.GET else ""
    return FileResponse(io.BytesIO(bytes(file.data)), as_attachment=not inline, filename=file.name, content_type=inline or "application/octet-stream")


def make_invitation(project, creator, email, name, role):
    token = secrets.token_urlsafe(32)
    with transaction.atomic():
        # ponytail: serialize access changes per project; finer locks only if invitation volume warrants it.
        Project.objects.select_for_update().get(pk=project.pk)
        if not ProjectMember.objects.filter(project=project, member=creator, role=20, is_active=True).exists():
            raise PermissionDenied
        Invitation.objects.filter(project=project, email=email, used_at__isnull=True).update(used_at=timezone.now())
        Invitation.objects.create(project=project, created_by=creator, email=email, name=name, role=role,
            token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=timezone.now() + timedelta(days=7))
    return token


@require_http_methods(["GET"])
@team_required(manager=True)
def team(request):
    return render(request, "desk/team.html", {
        "members": ProjectMember.objects.filter(project=request.project).select_related("member").order_by("-role", "member__first_name")})


@require_POST
@team_required(manager=True)
def revoke_member(request, pk):
    member = get_object_or_404(ProjectMember, project=request.project, pk=pk)
    if member.member_id == request.user.pk:
        raise PermissionDenied
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=request.project.pk)
        if not ProjectMember.objects.filter(project=request.project, member=request.user, role=20, is_active=True).exists():
            raise PermissionDenied
        member.is_active = False
        member.save()
        Invitation.objects.filter(project=request.project, email=member.member.email, used_at__isnull=True).update(used_at=timezone.now())
    messages.success(request, "Access removed. Slack will not automatically restore this account.")
    return redirect("team")


@team_required(manager=True)
def settings_page(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()[:100]
        group = request.POST.get("group")
        if group not in {"Topic", "Country"} or not name:
            messages.error(request, "Choose a category and enter a name.")
        elif Label.objects.filter(project=request.project, name__iexact=name).exists():
            messages.error(request, "That name already exists.")
        else:
            parent = get_object_or_404(Label, project=request.project, name=group, parent__isnull=True)
            Label.objects.create(project=request.project, workspace=request.project.workspace, parent=parent, name=name)
            messages.success(request, f"{name} added.")
        return redirect("settings")
    return render(request, "desk/settings.html", {"topics": Label.objects.filter(project=request.project, parent__name="Topic"),
        "countries": Label.objects.filter(project=request.project, parent__name="Country")})


def join(request, token):
    hashed = hashlib.sha256(token.encode()).hexdigest()
    with transaction.atomic():
        project_id = Invitation.objects.filter(token_hash=hashed).values_list("project_id", flat=True).first()
        if project_id:
            Project.objects.select_for_update().get(pk=project_id)
        invite = Invitation.objects.select_for_update(of=("self",)).filter(token_hash=hashed).select_related("project__workspace").first()
        if not invite or invite.used_at or invite.expires_at <= timezone.now():
            return render(request, "desk/auth.html", {"expired": True}, status=410)
        user = User.objects.filter(email=invite.email).first() or User(email=invite.email, username=invite.email)
        form = ActivateForm(request.POST or None, user=user, initial={"name": invite.name})
        if request.method == "POST" and form.is_valid():
            user.first_name = form.cleaned_data["name"]
            user.display_name = user.first_name
            user.set_password(form.cleaned_data["password"])
            user.is_active = True
            user.save()
            WorkspaceMember.objects.update_or_create(workspace=invite.project.workspace, member=user, defaults={"is_active": True, "role": invite.role})
            ProjectMember.objects.update_or_create(project=invite.project, member=user, defaults={"is_active": True, "role": invite.role})
            invite.used_at = timezone.now()
            invite.save(update_fields=["used_at"])
            login(request, user)
            messages.success(request, "You're in. Welcome to Ask Ops.")
            return redirect("queue")
    return render(request, "desk/auth.html", {"activation": True, "form": form, "invite": invite}, status=400 if form.errors else 200)


@require_http_methods(["GET", "POST"])
def slack_sign_in(request, token):
    hashed = hashlib.sha256(token.encode()).hexdigest()
    with transaction.atomic():
        project_id = SlackLogin.objects.filter(token_hash=hashed).values_list("project_id", flat=True).first()
        if project_id:
            Project.objects.select_for_update().get(pk=project_id)
        access = SlackLogin.objects.select_for_update(of=("self",)).select_related("user").filter(token_hash=hashed).first()
        if (not access or access.expires_at <= timezone.now() or not access.user.is_active or
            not ProjectMember.objects.filter(project_id=access.project_id, member=access.user, is_active=True).exists()):
            return render(request, "desk/auth.html", {"slack_expired": True}, status=410)
        # GET is safe for Slack link previews/scanners. Only the CSRF-protected button consumes access.
        if request.method == "POST":
            login(request, access.user)
            destination = reverse("detail", args=[access.issue_id]) if access.issue_id else "/?view=mine"
            access.delete()
            return redirect(destination)
    return render(request, "desk/auth.html", {"slack_access": access})


def sign_in(request):
    error = None
    response_status = 200
    if request.method == "POST":
        email = request.POST.get("email", "").lower().strip()[:254]
        # Vercel's trusted client IP header; raw forwarding headers are not trusted.
        ip = request.META.get("HTTP_X_VERCEL_FORWARDED_FOR") if os.environ.get("VERCEL") else None
        keys = ["email:" + email, "ip:" + (ip or request.META.get("REMOTE_ADDR", "unknown"))]
        blocked = False
        with transaction.atomic():
            for raw in keys:
                key = hashlib.sha256(raw.encode()).hexdigest()
                attempt, _ = LoginAttempt.objects.select_for_update().get_or_create(key=key, defaults={"since": timezone.now()})
                if attempt.since < timezone.now() - timedelta(minutes=15):
                    attempt.count, attempt.since = 0, timezone.now()
                blocked |= attempt.count >= (10 if raw.startswith("email:") else 100)
                attempt.count += 1
                attempt.save()
        if blocked:
            error, response_status = "Too many attempts. Please try again in 15 minutes.", 429
        else:
            user = authenticate(request, email=email, password=request.POST.get("password", "")[:1000])
            if user and ProjectMember.objects.filter(member=user, is_active=True, project__identifier="REV", project__workspace__slug="realadvisor").exists():
                login(request, user)
                LoginAttempt.objects.filter(key=hashlib.sha256(("email:" + email).encode()).hexdigest()).delete()
                return redirect("queue")
            error, response_status = "Email or password incorrect, or your access is not active.", 400
    return render(request, "desk/auth.html", {"error": error}, status=response_status)


@require_POST
def sign_out(request):
    logout(request)
    return redirect("login")


def health(request):
    return HttpResponse("ok", content_type="text/plain")
