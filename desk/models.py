import uuid
from django.conf import settings
from django.db import models


class RequestDetails(models.Model):
    issue = models.OneToOneField("db.Issue", on_delete=models.CASCADE, related_name="request_details")
    topic = models.ForeignKey("db.Label", on_delete=models.PROTECT, related_name="topic_requests")
    country = models.ForeignKey("db.Label", on_delete=models.PROTECT, related_name="country_requests")
    requested_deadline = models.DateField(null=True, blank=True)
    submission_key = models.UUIDField(default=uuid.uuid4, unique=True)


class Attachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="desk_attachments")
    name = models.CharField(max_length=180)
    # ponytail: private files <= 3 MiB in Postgres; move to object storage when file volume warrants it.
    data = models.BinaryField()
    size = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class Invitation(models.Model):
    token_hash = models.CharField(max_length=64, unique=True)
    email = models.EmailField()
    name = models.CharField(max_length=100)
    project = models.ForeignKey("db.Project", on_delete=models.CASCADE)
    role = models.PositiveSmallIntegerField(default=5, choices=[(5, "Requester"), (20, "Manager")])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True)


class ReadReceipt(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE)
    seen_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "issue"], name="desk_unique_receipt")]


class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    count = models.PositiveIntegerField(default=0)
    since = models.DateTimeField()
