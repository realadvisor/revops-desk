import uuid
from pathlib import PurePath
from django import forms
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from plane.db.models import Label, State, User

PRIORITIES = [("low", "Low — whenever possible"), ("medium", "Normal — plan it in"),
              ("high", "High — work is affected"), ("urgent", "Urgent — work is blocked")]
ATTACHMENT_EXTENSIONS = {"png", "jpg", "jpeg", "pdf", "csv", "txt"}
ATTACHMENT_MAX_BYTES = 3 * 1024 * 1024


class NameChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return getattr(obj, "name", None) or obj.first_name or obj.email


class AttachmentForm(forms.Form):
    attachment = forms.FileField(required=False, label="Attachment", help_text="PNG, JPG, PDF, CSV or TXT · up to 3 MB")

    def clean_attachment(self):
        file = self.cleaned_data.get("attachment")
        if file:
            if not file.size or file.size > ATTACHMENT_MAX_BYTES:
                raise forms.ValidationError("Choose a file between 1 byte and 3 MB.")
            if PurePath(file.name).suffix.lower().lstrip('.') not in ATTACHMENT_EXTENSIONS:
                raise forms.ValidationError("Please attach a PNG, JPG, PDF, CSV or TXT file.")
        return file


class RequestForm(AttachmentForm):
    title = forms.CharField(max_length=200, label="What do you need?", widget=forms.TextInput(attrs={"placeholder": "e.g. Add a country filter to the sales dashboard", "autofocus": True}))
    description = forms.CharField(min_length=10, max_length=20000, label="A little context", widget=forms.Textarea(attrs={"rows": 6, "placeholder": "What is happening, what should happen, and who is affected? Paste any useful links here."}))
    topic = NameChoice(queryset=Label.objects.none(), empty_label="Choose a topic")
    country = NameChoice(queryset=Label.objects.none(), empty_label="Choose a country")
    priority = forms.ChoiceField(choices=PRIORITIES, initial="medium", label="How urgent is it?")
    requested_deadline = forms.DateField(required=False, label="Needed by", widget=forms.DateInput(attrs={"type": "date"}), help_text="Optional. A requested date, not a delivery commitment.")
    submission_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)

    field_order = ["title", "description", "topic", "country", "priority", "requested_deadline", "attachment", "submission_key"]

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        for field, parent in [("topic", "Topic"), ("country", "Country")]:
            self.fields[field].queryset = Label.objects.filter(project=project, parent__name=parent).order_by("sort_order")

    def clean_requested_deadline(self):
        date = self.cleaned_data["requested_deadline"]
        if date and date < timezone.localdate():
            raise forms.ValidationError("Choose today or a future date.")
        return date


class ManagementForm(forms.Form):
    state = NameChoice(queryset=State.objects.none(), label="Status", empty_label=None)
    priority = forms.ChoiceField(choices=PRIORITIES, label="Priority")
    assignee = NameChoice(queryset=User.objects.none(), required=False, empty_label="Unassigned", label="Owner")
    target_date = forms.DateField(required=False, label="Delivery date", widget=forms.DateInput(attrs={"type": "date"}), help_text="Your committed date. Leave blank until agreed.")
    version = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["state"].queryset = State.objects.filter(project=project)
        self.fields["assignee"].queryset = User.objects.filter(member_project__project=project, member_project__is_active=True, member_project__role=20, is_active=True).distinct()


class CommentForm(forms.Form):
    comment = forms.CharField(max_length=10000, widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Add an update or answer a question…"}), label="Add an update")


class InviteForm(forms.Form):
    name = forms.CharField(max_length=100, label="Name")
    email = forms.EmailField(label="Work email")
    role = forms.TypedChoiceField(choices=[(5, "Requester — submit, follow and comment"), (20, "Manager — manage requests and team")], coerce=int, initial=5)

    def clean_email(self):
        return self.cleaned_data["email"].lower().strip()


class ActivateForm(forms.Form):
    name = forms.CharField(max_length=100, label="Your name", widget=forms.TextInput(attrs={"autocomplete": "name"}))
    password = forms.CharField(label="Create a password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}), help_text="At least 12 characters. A few memorable words work well.")
    confirm_password = forms.CharField(label="Confirm password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password, self.user)
        return password

    def clean(self):
        data = super().clean()
        if data.get("password") and data.get("password") != data.get("confirm_password"):
            self.add_error("confirm_password", "The passwords do not match.")
        return data
