"""Explicit Slack intake: signed commands/shortcuts, no message subscriptions."""
import hashlib
import hmac
import json
import time
import uuid
from urllib.error import URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from plane.db.models import Project, ProjectMember, User
from desk.forms import RequestForm, PRIORITIES
from desk.views import create_request

CALLBACK = 'revops_submit'
SALT = 'desk.slack.modal'


def slack_api(method, **payload):
    request = Request('https://slack.com/api/' + method, data=json.dumps(payload).encode(), headers={
        'Authorization': 'Bearer ' + settings.SLACK_BOT_TOKEN, 'Content-Type': 'application/json; charset=utf-8'})
    with urlopen(request, timeout=2) as response:
        data = json.load(response)
    if not data.get('ok'):
        raise URLError('Slack API request failed')
    return data


def plain(text):
    return {'type': 'plain_text', 'text': text}


def notice(text, link=None):
    view = {'type':'modal', 'title':plain('RevOps Desk'), 'close':plain('Close'),
            'blocks':[{'type':'section', 'text':plain(text)}]}
    if link:
        view['blocks'].append({'type':'actions', 'elements':[{'type':'button', 'action_id':'open_desk',
            'text':plain('Open ticket'), 'url':link}]})
    return view


def member(project, email):
    return User.objects.filter(email=email, is_active=True, member_project__project=project,
                               member_project__is_active=True).first()


def request_modal(project, actor, slack_user, text=''):
    form = RequestForm(project=project)
    blocks = [{'type':'section', 'text':plain('Your request and the text you submit will be visible to everyone in RevOps Desk.')}]
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
            return notice('This form has too many categories for Slack. Please use RevOps Desk in your browser.')
        options = [{'text':plain(label[:75]), 'value':str(value)} for value,label in choices]
        element = {'type':'static_select', 'action_id':'input', 'options':options}
        if name == 'priority':
            element['initial_option'] = next(o for o in options if o['value']=='medium')
        blocks.append({'type':'input', 'block_id':name, 'label':plain('Urgency' if name=='priority' else name.title()), 'element':element})
    blocks.append({'type':'input', 'block_id':'requested_deadline', 'optional':True,
        'label':plain('Needed by'), 'hint':plain('Optional requested date, not a delivery commitment.'),
        'element':{'type':'datepicker', 'action_id':'input'}})
    metadata = signing.dumps({'user':slack_user, 'actor':str(actor.pk), 'key':str(uuid.uuid4())}, salt=SALT)
    return {'type':'modal', 'callback_id':CALLBACK, 'title':plain('New RevOps request'), 'submit':plain('Submit request'),
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
            issue = create_request(project,actor,form.cleaned_data)
            link = request.build_absolute_uri(f'/requests/{issue.pk}/')
            return JsonResponse({'response_action':'update', 'view':notice(f'REV-{issue.sequence_id} submitted. You can follow its progress and add updates in RevOps Desk.',link)})
        if not (payload.get('command') == '/revops' or
                payload.get('type') in ('shortcut','message_action') and payload.get('callback_id') in ('revops_create','revops_message')):
            return HttpResponse(status=400)
        profile = slack_api('users.info', user=slack_user)['user']
        email = profile.get('profile',{}).get('email','').strip().lower()
        actor = member(project,email) if profile.get('id') == slack_user and profile.get('team_id') == settings.SLACK_TEAM_ID and not any(
            profile.get(flag) for flag in ('deleted','is_bot','is_app_user','is_restricted','is_ultra_restricted','is_stranger')) else None
        if actor:
            text = payload.get('message',{}).get('text','') if payload.get('type') == 'message_action' else payload.get('text','')
            modal = request_modal(project,actor,slack_user,text)
        else:
            modal = notice('Ask your RevOps manager to invite your Slack work email to RevOps Desk first. Guest and external Slack accounts cannot submit requests.')
        slack_api('views.open', trigger_id=payload['trigger_id'], view=modal)
        return HttpResponse()
    except (signing.BadSignature, PermissionDenied):
        return HttpResponse('Not authorized.',status=403)
    except (ValueError,KeyError,TypeError,AttributeError):
        return HttpResponse('Invalid Slack request.',status=400)
    except (URLError,TimeoutError):
        return JsonResponse({'response_type':'ephemeral', 'text':'Slack could not open the form. Please try /revops again, or use RevOps Desk in your browser.'},status=503)
