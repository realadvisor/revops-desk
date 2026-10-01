import hashlib
import hmac
import json
import io
from email.message import Message
from urllib.error import URLError
import time
from datetime import timedelta
from urllib.parse import urlencode
from unittest.mock import patch

from django.test import Client, TestCase, SimpleTestCase, override_settings
from django.core.management import call_command
from django.utils import timezone
from plane.db.models import Project, Issue, Label, ProjectMember, User, WorkspaceMember
from desk.models import SlackLogin, Invitation, Attachment


@override_settings(SECURE_SSL_REDIRECT=False, SLACK_SIGNING_SECRET='test-secret', SLACK_BOT_TOKEN='test-token', SLACK_TEAM_ID='TTEST', SLACK_APP_ID='ATEST',
    STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class SlackTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('bootstrap_desk', email='owner@realadvisor.com', verbosity=0)
        cls.project = Project.objects.get(identifier='REV')

    def post(self, payload, *, age=0, signature=None):
        body = urlencode({'payload': json.dumps(payload)}).encode()
        timestamp = str(int(time.time()) - age)
        digest = hmac.new(b'test-secret', b'v0:' + timestamp.encode() + b':' + body, hashlib.sha256).hexdigest()
        return Client(enforce_csrf_checks=True).post('/slack/interactions/', body,
            content_type='application/x-www-form-urlencoded', HTTP_X_SLACK_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_SLACK_SIGNATURE=signature or 'v0=' + digest)

    def start(self, **extra):
        return {'type':'shortcut', 'callback_id':'revops_create', 'team':{'id':'TTEST'},
                'api_app_id':'ATEST', 'user':{'id':'UTEST'}, 'trigger_id':'test-trigger', **extra}

    def test_signatures_workspace_and_guests_fail_closed(self):
        with patch('desk.slack.slack_api') as api:
            self.assertEqual(self.post(self.start(), signature='v0=bad').status_code, 403)
            self.assertEqual(self.post(self.start(), age=301).status_code, 403)
            self.assertEqual(self.post(self.start(team={'id':'OTHER'})).status_code, 403)
            self.assertEqual(self.post(self.start(api_app_id='OTHER')).status_code, 403)
            api.assert_not_called()
        for flag in ('deleted','is_bot','is_app_user','is_restricted','is_ultra_restricted','is_stranger'):
            with patch('desk.slack.slack_api', return_value={'user':{'id':'UTEST','team_id':'TTEST',flag:True,'profile':{'email':'outsider@realadvisor.com'}}}) as api:
                self.post(self.start())
                self.assertNotIn('submit', api.call_args.kwargs['view'])
        self.assertFalse(User.objects.filter(email='outsider@realadvisor.com').exists())
        self.assertEqual(Issue.objects.count(),0)

    def open_form(self, email='owner@realadvisor.com'):
        def api(method, **kwargs):
            if method == 'users.info':
                return {'user':{'id':'UTEST','team_id':'TTEST','profile':{'email':email,'real_name':'Slack Person'}}}
            self.modal = kwargs['view']
            return {'ok':True}
        with patch('desk.slack.slack_api', side_effect=api):
            self.assertEqual(self.post(self.start()).status_code,200)
        return self.modal

    def submission(self, modal):
        values={
            'title': {'input': {'value':'Slack request'}},
            'description': {'input': {'value':'Please add a filter <script>unsafe</script>'}},
            'topic': {'input': {'selected_option':{'value':str(Label.objects.get(name='Close',project=self.project).pk)}}},
            'country': {'input': {'selected_option':{'value':str(Label.objects.get(name='France',project=self.project).pk)}}},
            'priority': {'input': {'selected_option':{'value':'high'}}},
            'requested_deadline': {'input': {'selected_date':'2030-01-01'}},
        }
        return self.start(type='view_submission',view={'id':'VTEST','callback_id':'revops_submit', 'private_metadata':modal['private_metadata'],'state':{'values':values}})

    def test_modal_submit_is_shared_validated_and_idempotent(self):
        modal=self.open_form()
        body=self.submission(modal)
        response=self.post(body)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['response_action'],'update')
        ticket=Issue.objects.get(name='Slack request')
        self.assertEqual(ticket.created_by,self.project.project_lead)
        self.assertEqual(ticket.priority,'high')
        self.assertIn('&lt;script&gt;',ticket.description_html)
        self.assertEqual(str(ticket.request_details.requested_deadline),'2030-01-01')
        self.assertIsNone(ticket.target_date)
        self.assertIn('/slack/access/', json.dumps(response.json()))
        self.post(body)
        self.assertEqual(Issue.objects.count(),1)
        # Identity and access are checked again at submission.
        body['user']['id']='OTHER'
        self.assertEqual(self.post(body).status_code,403)
        body['user']['id']='UTEST'
        ProjectMember.objects.filter(project=self.project,member=ticket.created_by).update(is_active=False)
        self.assertEqual(self.post(body).status_code,403)

    def test_first_slack_use_needs_no_invitation_and_preserves_access_controls(self):
        modal = self.open_form('new.person@realadvisor.com')
        actor = User.objects.get(email='new.person@realadvisor.com')
        self.assertFalse(actor.has_usable_password())
        self.assertEqual(ProjectMember.objects.get(project=self.project,member=actor).role,5)
        self.assertEqual(WorkspaceMember.objects.get(workspace=self.project.workspace,member=actor).role,5)
        self.assertFalse(Invitation.objects.exists())
        self.assertEqual(self.post(self.submission(modal)).json()['response_action'],'update')
        self.assertEqual(Issue.objects.get().created_by,actor)
        self.open_form('new.person@realadvisor.com')
        self.assertEqual(User.objects.filter(email=actor.email).count(),1)
        ProjectMember.objects.filter(project=self.project,member=actor).update(is_active=False)
        self.assertNotIn('submit',self.open_form(actor.email))
        self.assertFalse(ProjectMember.objects.get(project=self.project,member=actor).is_active)
        self.open_form()
        self.assertEqual(ProjectMember.objects.get(project=self.project,member=self.project.project_lead).role,20)
        actor.is_active=False
        actor.save()
        self.assertNotIn('submit',self.open_form(actor.email))

    def test_passwordless_ticket_access_is_csrf_protected_one_use_and_expires(self):
        response = self.post(self.submission(self.open_form('new.person@realadvisor.com'))).json()
        url = response['view']['blocks'][-1]['elements'][0]['url']
        browser = Client(enforce_csrf_checks=True)
        self.assertEqual(browser.get(url).status_code,200)
        self.assertEqual(browser.get(url).status_code,200)  # Scanners cannot consume it.
        self.assertNotIn('_auth_user_id',browser.session)
        self.assertEqual(browser.post(url).status_code,403)
        response = browser.post(url,HTTP_X_CSRFTOKEN=browser.cookies['csrftoken'].value)
        self.assertEqual(response.status_code,302)
        self.assertEqual(response.url,f'/requests/{Issue.objects.get().pk}/')
        self.assertEqual(browser.get(response.url).status_code,200)
        self.assertEqual(browser.get('/team/').status_code,403)
        self.assertEqual(Client().post(url).status_code,410)
        modal=self.open_form()
        desk_url=modal['blocks'][-1]['elements'][0]['url']
        SlackLogin.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(Client().post(desk_url).status_code,410)
        modal=self.open_form()
        desk_url=modal['blocks'][-1]['elements'][0]['url']
        ProjectMember.objects.filter(project=self.project,member=self.project.project_lead).update(is_active=False)
        self.assertEqual(Client().post(desk_url).status_code,410)

    def test_field_errors_preserve_modal_without_creating_ticket(self):
        body=self.submission(self.open_form())
        body['view']['state']['values']['requested_deadline']['input']['selected_date']='2020-01-01'
        response=self.post(body).json()
        self.assertEqual(response['response_action'],'errors')
        self.assertIn('requested_deadline',response['errors'])
        self.assertEqual(Issue.objects.count(),0)
        body['view']['private_metadata']='tampered'
        self.assertEqual(self.post(body).status_code,403)

    def test_slack_attachments_are_saved_privately_and_retries_do_not_duplicate(self):
        modal=self.open_form()
        upload=next(block for block in modal['blocks'] if block.get('block_id')=='attachments')
        self.assertEqual(upload['element']['type'],'file_input')
        body=self.submission(modal)
        files=[{'id':f'FTEST{i}','name':f'context{i}.txt','size':5,'user':'UTEST','user_team':'TTEST',
            'url_private_download':f'https://files.slack.com/files-pri/TTEST-FTEST{i}/download/context.txt'} for i in range(3)]
        body['view']['state']['values']['attachments']={'input':{'type':'file_input','files':files}}
        def download(request, **kwargs):
            self.assertEqual(request.get_header('Authorization'),'Bearer test-token')
            response=io.BytesIO(b'hello')
            response.headers=Message()
            response.headers['Content-Type']='text/plain'
            return response
        with patch('desk.slack.build_opener') as opener:
            opener.return_value.open.side_effect=download
            self.assertEqual(self.post(body).json()['response_action'],'update')
            self.assertEqual(self.post(body).json()['response_action'],'update')
            self.assertEqual(opener.return_value.open.call_count,3)
        self.assertEqual(Issue.objects.count(),1)
        self.assertEqual(Attachment.objects.count(),3)
        file=Attachment.objects.first()
        self.assertEqual(bytes(file.data),b'hello')
        self.assertEqual(file.uploaded_by,self.project.project_lead)
        self.assertEqual(Client().get(f'/files/{file.pk}/').status_code,302)
        browser=Client()
        browser.force_login(file.uploaded_by)
        self.assertEqual(b''.join(browser.get(f'/files/{file.pk}/').streaming_content),b'hello')

    def test_invalid_or_unavailable_files_preserve_form_without_partial_ticket(self):
        body=self.submission(self.open_form())
        good={'id':'FTEST','name':'context.txt','size':5,'user':'UTEST','user_team':'TTEST',
            'url_private_download':'https://files.slack.com/files-pri/TTEST-FTEST/download/context.txt'}
        for change in ({'size':3145729},{'name':'run.exe'},{'user':'OTHER'},{'user_team':'OTHER'},
                       {'url_private_download':'https://evil.example/context.txt'}):
            body['view']['state']['values']['attachments']={'input':{'files':[{**good,**change}]}}
            with patch('desk.slack.build_opener') as opener:
                self.assertIn('attachments',self.post(body).json()['errors'])
                opener.assert_not_called()
        body['view']['state']['values']['attachments']={'input':{'files':[good]}}
        with patch('desk.slack.build_opener') as opener:
            opener.return_value.open.side_effect=URLError('download unavailable')
            self.assertIn('attachments',self.post(body).json()['errors'])
        self.assertEqual(Issue.objects.count(),0)
        self.assertEqual(Attachment.objects.count(),0)

    def test_command_and_message_shortcut_open_reviewable_forms(self):
        captured=[]
        def api(method, **kwargs):
            if method=='users.info':
                return {'user':{'id':'UTEST','team_id':'TTEST','profile':{'email':'owner@realadvisor.com'}}}
            captured.append(kwargs['view'])
            return {'ok':True}
        with patch('desk.slack.slack_api',side_effect=api):
            command={'command':'/askops','api_app_id':'ATEST','team_id':'TTEST','user_id':'UTEST','text':'Dashboard filter','trigger_id':'trigger'}
            self.assertEqual(self.post(command).status_code,200)
            self.assertEqual(captured[-1]['blocks'][1]['element']['initial_value'],'Dashboard filter')
            self.assertEqual(self.post({**command,'command':'/revops'}).status_code,200)
            self.assertEqual(self.post({**command,'command':'/other'}).status_code,400)
            self.assertEqual(self.post(self.start(type='message_action',callback_id='revops_message',message={'text':'Please fix this invoice.\nHere is the context.'})).status_code,200)
            self.assertEqual(captured[-1]['blocks'][2]['element']['initial_value'],'Please fix this invoice.\nHere is the context.')
        self.assertEqual(Issue.objects.count(),0)


@override_settings(SLACK_BOT_TOKEN='test-token')
class SlackTransportTests(SimpleTestCase):
    def test_slack_methods_use_form_encoding_with_json_nested_views(self):
        import io
        from urllib.parse import parse_qs
        from desk.slack import slack_api
        captured=[]
        def response(request, **kwargs):
            captured.append(request)
            return io.BytesIO(b'{"ok":true}')
        with patch('desk.slack.urlopen',side_effect=response):
            slack_api('users.info',user='UTEST')
            slack_api('views.open',trigger_id='trigger',view={'type':'modal'})
        self.assertEqual(captured[0].get_header('Content-type'),'application/x-www-form-urlencoded')
        self.assertEqual(parse_qs(captured[0].data.decode()),{'user':['UTEST']})
        self.assertEqual(json.loads(parse_qs(captured[1].data.decode())['view'][0]),{'type':'modal'})
