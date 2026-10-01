"""Explicit Slack intake: signed commands/shortcuts, no message subscriptions."""
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import PurePath
from http.client import HTTPException
from urllib.error import URLError
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from plane.db.models import Project, ProjectMember, WorkspaceMember, User
from desk.models import SlackLogin, RequestDetails
from desk.forms import RequestForm, AttachmentForm, PRIORITIES, ATTACHMENT_EXTENSIONS, ATTACHMENT_MAX_BYTES
from desk.views import create_request

CALLBACK = 'revops_submit'
SALT = 'desk.slack.modal'


class NoFileRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the bot credential to a redirected host.


def read_slack_file(file):
    url = file.get('url_private_download') or file.get('url_private', '')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'files.slack.com' or
        not parsed.path.startswith(f"/files-pri/{settings.SLACK_TEAM_ID}-{file['id']}/")):
        raise ValidationError('This file is not a supported Slack upload. Please upload it again.')
    request = Request(url, headers={'Authorization':'Bearer ' + settings.SLACK_BOT_TOKEN})
    with build_opener(NoFileRedirect()).open(request, timeout=1) as response:
        data = response.read(ATTACHMENT_MAX_BYTES + 1)
        if response.headers.get_content_type() == 'text/html':
            raise ValidationError('Slack could not provide this file. Please upload it again.')
    if len(data) != file['size']:
        raise ValidationError('The file download was incomplete or too large. Please try again.')
    form = AttachmentForm({}, {'attachment':SimpleUploadedFile(file['name'],data)})
    if not form.is_valid():
        raise ValidationError(form.errors['attachment'][0])
    return form.cleaned_data['attachment']


def slack_attachments(files, slack_user):
    if not isinstance(files,list) or len(files) > 3:
        raise ValidationError('Choose up to 3 attachments.')
    for file in files:
        if (not isinstance(file,dict) or not file.get('id') or file.get('user') != slack_user or
            file.get('user_team') != settings.SLACK_TEAM_ID or file.get('is_external')):
            raise ValidationError('Please upload files from your own RealAdvisor Slack account.')
        if not isinstance(file.get('size'),int) or not 0 < file['size'] <= ATTACHMENT_MAX_BYTES:
            raise ValidationError('Each attachment must be between 1 byte and 3 MB.')
        if PurePath(file.get('name','')).suffix.lower().lstrip('.') not in ATTACHMENT_EXTENSIONS:
            raise ValidationError('Please attach PNG, JPG, PDF, CSV or TXT files.')
    # ponytail: 3 small parallel downloads fit Slack's short acknowledgement window; use a durable queue for larger uploads.
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(read_slack_file,files))


def slack_api(method, **payload):
    request = Request('https://slack.com/api/' + method, data=urlencode({
        key:json.dumps(value) if isinstance(value,(dict,list)) else value for key,value in payload.items()
    }).encode(), headers={
        'Authorization': 'Bearer ' + settings.SLACK_BOT_TOKEN, 'Content-Type': 'application/x-www-form-urlencoded'})
    with urlopen(request, timeout=2) as response:
        data = json.load(response)
    if not data.get('ok'):
        raise URLError('Slack API ' + method + ': ' + str(data.get('error','unknown_error'))[:100])
    return data


def notify(issue, actor, text, link):
    """Private DM to the requester about their own ticket; never blocks or fails the save."""
    requester = issue.created_by
    if not settings.SLACK_BOT_TOKEN or not requester or requester.pk == actor.pk:
        return
    def safe(value):  # Slack control characters: no injected links or mentions.
        return str(value).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    try:
        # ponytail: two synchronous Slack calls (2 s timeout each) per update; use a queue if saves feel slow.
        user = slack_api('users.lookupByEmail', email=requester.email)['user']['id']
        slack_api('chat.postMessage', channel=user,
            text=f'*REV-{issue.sequence_id} · {safe(issue.name)}*\n{safe(text)}\n<{link}|Open ticket>')
    except (URLError, TimeoutError, HTTPException, KeyError, TypeError) as error:
        logging.getLogger(__name__).warning('Slack notification failure: %s', error)


def plain(text):
    return {'type': 'plain_text', 'text': text}


def notice(text, link=None):
    view = {'type':'modal', 'title':plain('Ask Ops'), 'close':plain('Close'),
            'blocks':[{'type':'section', 'text':plain(text)}]}
    if link:
        view['blocks'].append({'type':'actions', 'elements':[{'type':'button', 'action_id':'open_desk',
            'text':plain('Open ticket'), 'url':link}]})
    return view


def member(project, email):
    return User.objects.filter(email=email, is_active=True, member_project__project=project,
                               member_project__is_active=True).first()


@transaction.atomic
def provision_member(project, profile):
    """Only called with a full member profile fetched from the configured Slack workspace."""
    email = profile.get('profile',{}).get('email','').strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        return None
    Project.objects.select_for_update().get(pk=project.pk)
    actor = User.objects.filter(email=email).first()
    if actor and (not actor.is_active or
        ProjectMember.objects.filter(project=project, member=actor, is_active=False).exists() or
        WorkspaceMember.objects.filter(workspace=project.workspace, member=actor, is_active=False).exists()):
        return None  # Removing access must survive another Slack command.
    if not actor:
        name = (profile.get('profile',{}).get('real_name') or profile.get('real_name') or email.split('@')[0])[:100]
        actor = User(email=email, username=str(uuid.uuid4()), first_name=name, display_name=name)
        actor.set_unusable_password()
        actor.save()
    WorkspaceMember.objects.get_or_create(workspace=project.workspace, member=actor, defaults={'role':5})
    ProjectMember.objects.get_or_create(project=project, member=actor, defaults={'role':5})
    return actor


def login_link(request, project, actor, issue=None):
    token = secrets.token_urlsafe(32)
    SlackLogin.objects.filter(expires_at__lte=timezone.now()).delete()
    SlackLogin.objects.create(token_hash=hashlib.sha256(token.encode()).hexdigest(), user=actor,
        project=project, issue=issue, expires_at=timezone.now() + timedelta(minutes=15))
    return request.build_absolute_uri(reverse('slack_login', args=[token]))


def request_modal(project, actor, slack_user, text=''):
    form = RequestForm(project=project)
    blocks = [{'type':'section', 'text':plain('Your request and the text you submit will be visible to everyone in Ask Ops.')}]
    for name, label, multiline, limit in [('title','What do you need?',False,200), ('description','A little context',True,3000)]:
        element = {'type':'plain_text_input', 'action_id':'input', 'multiline':multiline, 'max_length':limit}
        initial = text[:limit] if multiline else (text.splitlines()[0][:limit] if text else '')
        if initial:
            element['initial_value'] = initial
        blocks.append({'type':'input', 'block_id':name, 'label':plain(label), 'element':element})
    for name in ('topic','country','priority'):
        choices = PRIORITIES if name == 'priority' else [(str(row.pk),row.name) for row in form.fields[name].queryset]
        # Slack caps static selects at 100 options; fail visibly rather than omit maintained categories.
        if not 0 < len(choices) <= 100:
            return notice('This form has too many categories for Slack. Please use Ask Ops in your browser.')
        options = [{'text':plain(label[:75]), 'value':str(value)} for value,label in choices]
        element = {'type':'static_select', 'action_id':'input', 'options':options}
        if name == 'priority':
            element['initial_option'] = next(o for o in options if o['value']=='medium')
        blocks.append({'type':'input', 'block_id':name, 'label':plain('Urgency' if name=='priority' else name.title()), 'element':element})
    blocks.append({'type':'input', 'block_id':'requested_deadline', 'optional':True,
        'label':plain('Needed by'), 'hint':plain('Optional requested date, not a delivery commitment.'),
        'element':{'type':'datepicker', 'action_id':'input'}})
    blocks.append({'type':'input', 'block_id':'attachments', 'optional':True,
        'label':plain('Attachments'), 'hint':plain('Up to 3 files, 3 MB each. PNG, JPG, PDF, CSV or TXT. Files are shared with the ticket.'),
        'element':{'type':'file_input', 'action_id':'input', 'filetypes':sorted(ATTACHMENT_EXTENSIONS), 'max_files':3}})
    metadata = signing.dumps({'user':slack_user, 'actor':str(actor.pk), 'key':str(uuid.uuid4())}, salt=SALT)
    return {'type':'modal', 'callback_id':CALLBACK, 'title':plain('New Ask Ops request'), 'submit':plain('Submit request'),
            'close':plain('Cancel'), 'private_metadata':metadata, 'blocks':blocks}


def verified_payload(request):
    if not all((settings.SLACK_SIGNING_SECRET, settings.SLACK_BOT_TOKEN, settings.SLACK_TEAM_ID, settings.SLACK_APP_ID)):
        return None
    timestamp = request.headers.get('X-Slack-Request-Timestamp','')
    try:
        fresh = abs(time.time() - int(timestamp)) <= 300
    except ValueError:
        fresh = False
    if not fresh or len(request.body) > 128 * 1024:
        raise PermissionDenied
    expected = 'v0=' + hmac.new(settings.SLACK_SIGNING_SECRET.encode(),
        b'v0:' + timestamp.encode() + b':' + request.body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, request.headers.get('X-Slack-Signature','')):
        raise PermissionDenied
    payload = json.loads(request.POST['payload']) if 'payload' in request.POST else request.POST.dict()
    if not isinstance(payload,dict):
        raise ValueError('Invalid payload')
    team = payload.get('team',{}).get('id') or payload.get('team_id')
    if team != settings.SLACK_TEAM_ID or payload.get('api_app_id') != settings.SLACK_APP_ID:
        raise PermissionDenied
    return payload


@csrf_exempt  # Slack HMAC authenticates the exact body, workspace and app; browser sessions cannot authorize this route.
@require_POST
def interactions(request):
    try:
        payload = verified_payload(request)
        if payload is None:
            return HttpResponse('Slack intake is not configured.', status=503)
        if payload.get('type') == 'block_actions':
            return HttpResponse()  # URL button acknowledgement; no mutations.
        project = Project.objects.select_related('workspace','default_state').get(identifier='REV', workspace__slug='realadvisor')
        slack_user = payload.get('user',{}).get('id') or payload.get('user_id')
        if not slack_user:
            raise PermissionDenied
        if payload.get('type') == 'view_submission':
            view = payload['view']
            if view.get('callback_id') != CALLBACK:
                return HttpResponse(status=400)
            metadata = signing.loads(view['private_metadata'], salt=SALT, max_age=3600)
            actor = User.objects.filter(pk=metadata['actor'],is_active=True).first()
            if metadata['user'] != slack_user or not actor or not member(project,actor.email):
                raise PermissionDenied
            values = view['state']['values']
            data = {'submission_key':metadata['key']}
            for key in ('title','description','topic','country','priority','requested_deadline'):
                field = values.get(key,{}).get('input',{})
                data[key] = (field.get('selected_option') or {}).get('value') if key in ('topic','country','priority') else field.get('selected_date' if key=='requested_deadline' else 'value')
            form = RequestForm(data,project=project)
            if not form.is_valid():
                return JsonResponse({'response_action':'errors', 'errors':{key:str(errors[0]) for key,errors in form.errors.items()}})
            if not RequestDetails.objects.filter(submission_key=data['submission_key']).exists():
                try:
                    form.cleaned_data['slack_attachments'] = slack_attachments(values.get('attachments',{}).get('input',{}).get('files') or [],slack_user)
                except ValidationError as error:
                    return JsonResponse({'response_action':'errors', 'errors':{'attachments':error.messages[0]}})
                except (URLError,TimeoutError,HTTPException):
                    return JsonResponse({'response_action':'errors', 'errors':{'attachments':'Slack could not download an attachment in time. Please try submitting again. Your ticket has not been created.'}})
            issue = create_request(project,actor,form.cleaned_data)
            link = login_link(request,project,actor,issue)
            return JsonResponse({'response_action':'update', 'view':notice(f'REV-{issue.sequence_id} submitted. You can follow its progress and add updates in Ask Ops.',link)})
        if not (payload.get('command') == '/revops' or
                payload.get('type') in ('shortcut','message_action') and payload.get('callback_id') in ('revops_create','revops_message')):
            return HttpResponse(status=400)
        profile = slack_api('users.info', user=slack_user)['user']
        actor = provision_member(project,profile) if profile.get('id') == slack_user and profile.get('team_id') == settings.SLACK_TEAM_ID and not any(
            profile.get(flag) for flag in ('deleted','is_bot','is_app_user','is_restricted','is_ultra_restricted','is_stranger')) else None
        if actor:
            text = payload.get('message',{}).get('text','') if payload.get('type') == 'message_action' else payload.get('text','')
            modal = request_modal(project,actor,slack_user,text)
            modal['blocks'].append({'type':'actions', 'elements':[{'type':'button', 'action_id':'open_desk',
                'text':plain('My requests'), 'url':login_link(request,project,actor)}]})
        else:
            modal = notice('Use your active RealAdvisor Slack member account to submit a request. If your Desk access was removed, contact your RevOps manager. Guest and external accounts cannot access the shared desk.')
        slack_api('views.open', trigger_id=payload['trigger_id'], view=modal)
        return HttpResponse()
    except (signing.BadSignature, PermissionDenied):
        return HttpResponse('Not authorized.',status=403)
    except (ValueError,KeyError,TypeError,AttributeError):
        return HttpResponse('Invalid Slack request.',status=400)
    except (URLError,TimeoutError) as error:
        logging.getLogger(__name__).warning("Slack intake API failure: %s", error)
        return JsonResponse({'response_type':'ephemeral', 'text':'Slack could not open the form. Please try /revops again, or use Ask Ops in your browser.'},status=503)
