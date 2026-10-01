import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.core.management import call_command
from plane.db.models import Project, Issue, Label, ProjectMember


@override_settings(SECURE_SSL_REDIRECT=False, SLACK_SIGNING_SECRET='test-secret', SLACK_BOT_TOKEN='test-token', SLACK_TEAM_ID='TTEST', SLACK_APP_ID='ATEST')
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

    def test_signatures_workspace_and_unknown_users_fail_closed(self):
        with patch('desk.slack.slack_api') as api:
            self.assertEqual(self.post(self.start(), signature='v0=bad').status_code, 403)
            self.assertEqual(self.post(self.start(), age=301).status_code, 403)
            self.assertEqual(self.post(self.start(team={'id':'OTHER'})).status_code, 403)
            self.assertEqual(self.post(self.start(api_app_id='OTHER')).status_code, 403)
            api.assert_not_called()
        with patch('desk.slack.slack_api', return_value={'user':{'id':'UTEST','team_id':'TTEST','profile':{'email':'outsider@realadvisor.com'}}}) as api:
            self.post(self.start())
            self.assertNotIn('submit', api.call_args.kwargs['view'])
        self.assertEqual(Issue.objects.count(),0)

    def open_form(self):
        def api(method, **kwargs):
            if method == 'users.info':
                return {'user':{'id':'UTEST','team_id':'TTEST','profile':{'email':'owner@realadvisor.com'}}}
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
        self.assertIn(str(ticket.pk), json.dumps(response.json()))
        self.post(body)
        self.assertEqual(Issue.objects.count(),1)
        # Identity and access are checked again at submission.
        body['user']['id']='OTHER'
        self.assertEqual(self.post(body).status_code,403)
        body['user']['id']='UTEST'
        ProjectMember.objects.filter(project=self.project,member=ticket.created_by).update(is_active=False)
        self.assertEqual(self.post(body).status_code,403)

    def test_field_errors_preserve_modal_without_creating_ticket(self):
        body=self.submission(self.open_form())
        body['view']['state']['values']['requested_deadline']['input']['selected_date']='2020-01-01'
        response=self.post(body).json()
        self.assertEqual(response['response_action'],'errors')
        self.assertIn('requested_deadline',response['errors'])
        self.assertEqual(Issue.objects.count(),0)
        body['view']['private_metadata']='tampered'
        self.assertEqual(self.post(body).status_code,403)

    def test_command_and_message_shortcut_open_reviewable_forms(self):
        captured=[]
        def api(method, **kwargs):
            if method=='users.info':
                return {'user':{'id':'UTEST','team_id':'TTEST','profile':{'email':'owner@realadvisor.com'}}}
            captured.append(kwargs['view'])
            return {'ok':True}
        with patch('desk.slack.slack_api',side_effect=api):
            command={'command':'/revops','api_app_id':'ATEST','team_id':'TTEST','user_id':'UTEST','text':'Dashboard filter','trigger_id':'trigger'}
            self.assertEqual(self.post(command).status_code,200)
            self.assertEqual(captured[-1]['blocks'][1]['element']['initial_value'],'Dashboard filter')
            self.assertEqual(self.post(self.start(type='message_action',callback_id='revops_message',message={'text':'Please fix this invoice.\nHere is the context.'})).status_code,200)
            self.assertEqual(captured[-1]['blocks'][2]['element']['initial_value'],'Please fix this invoice.\nHere is the context.')
        self.assertEqual(Issue.objects.count(),0)
