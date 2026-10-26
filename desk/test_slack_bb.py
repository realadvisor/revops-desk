import hashlib
import hmac
import io
import json
import time
from urllib.error import URLError
from urllib.parse import urlencode
from unittest.mock import patch

from django.test import Client, SimpleTestCase, override_settings


@override_settings(
    SECURE_SSL_REDIRECT=False, SLACK_SIGNING_SECRET='test-secret',
    SLACK_BOT_TOKEN='test-token', SLACK_TEAM_ID='TTEST', SLACK_APP_ID='ATEST',
    BB_SLACK_USER_ID='UTHEO', BB_WEBHOOK_TOKEN='bb-test-token',
)
class SendToBBTests(SimpleTestCase):
    def post(self, **changes):
        payload = {
            'type': 'message_action', 'callback_id': 'revops_send_to_bb',
            'team': {'id': 'TTEST'}, 'user': {'id': 'UTHEO'},
            'channel': {'id': 'C123'}, 'trigger_id': 'trigger',
            'message_ts': '1792500001.000002',
            'message': {'ts': '1792500001.000002', 'text': 'Investigate this'},
            **changes,
        }
        raw = urlencode({'payload': json.dumps(payload)}).encode()
        timestamp = str(int(time.time()))
        signature = 'v0=' + hmac.new(
            b'test-secret', b'v0:' + timestamp.encode() + b':' + raw, hashlib.sha256,
        ).hexdigest()
        return Client().post(
            '/slack/interactions/', raw, content_type='application/x-www-form-urlencoded',
            HTTP_X_SLACK_REQUEST_TIMESTAMP=timestamp, HTTP_X_SLACK_SIGNATURE=signature,
        )

    def accepted(self):
        response = io.BytesIO(b'{"accepted":true}')
        response.status = 202
        return response

    def submit(self, api, prompt=None, reply=False, **changes):
        view = api.call_args.kwargs['view']
        values = {'additional_prompt': {'input': {'value': prompt}}}
        if reply is not None:
            values['reply_in_thread'] = {'input': {'selected_options': (
                [{'value': 'reply'}] if reply else [])}}
        return self.post(type='view_submission', view={
            'id': 'VTEST', 'callback_id': 'revops_send_to_bb_submit',
            'private_metadata': view['private_metadata'],
            'state': {'values': values},
        }, **changes)

    @patch('desk.slack.slack_api')
    @patch('desk.slack.urlopen')
    def test_opening_or_cancelling_does_not_dispatch(self, network, api):
        self.assertEqual(self.post().status_code, 200)
        network.assert_not_called()
        view = api.call_args.kwargs['view']
        field = next(block for block in view['blocks'] if block['type'] == 'input')
        self.assertTrue(field['optional'])
        self.assertTrue(field['element']['multiline'])
        reply = next(block for block in view['blocks'] if block.get('block_id') == 'reply_in_thread')
        self.assertTrue(reply['optional'])
        self.assertEqual(reply['element']['type'], 'checkboxes')
        self.assertFalse(reply['element'].get('initial_options'))
        self.assertEqual(self.post(type='view_closed', view=view).status_code, 200)
        network.assert_not_called()

    @patch('desk.slack.slack_api')
    @patch('desk.slack.urlopen')
    def test_prompt_and_reply_context_reach_bb_with_stable_retry_key(self, network, api):
        network.side_effect = lambda *a, **kw: self.accepted()
        self.post(message={'ts': '1792500001.000002', 'thread_ts': '1792500000.000001'})
        result = self.submit(api, 'Analyze only.\nDo not change records.')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['response_action'], 'update')
        self.assertIn('Queued', json.dumps(result.json()['view']))
        request = network.call_args.args[0]
        self.assertTrue(request.get_header('Idempotency-key').startswith('TTEST:C123:1792500001.000002:'))
        self.assertEqual(request.get_header('Authorization'), 'Bearer bb-test-token')
        self.assertEqual(json.loads(request.data), {
            'type': 'slack_message_shortcut', 'team_id': 'TTEST', 'channel_id': 'C123',
            'message_ts': '1792500001.000002', 'thread_ts': '1792500000.000001',
            'requested_by': 'UTHEO', 'additional_prompt': 'Analyze only.\nDo not change records.',
            'reply_in_thread': False,
        })
        self.submit(api, 'Analyze only.\nDo not change records.')
        self.assertEqual(network.call_args.args[0].get_header('Idempotency-key'),
                         request.get_header('Idempotency-key'))
        self.submit(api, 'A changed prompt')
        self.assertNotEqual(network.call_args.args[0].get_header('Idempotency-key'),
                            request.get_header('Idempotency-key'))

    @patch('desk.slack.slack_api')
    @patch('desk.slack.urlopen')
    def test_empty_prompt_has_stable_identity_and_no_slack_reply(self, network, api):
        network.side_effect = lambda *a, **kw: self.accepted()
        self.post()
        for prompt in (None, '', '   '):
            self.assertEqual(self.submit(api, prompt).status_code, 200)
            event = json.loads(network.call_args.args[0].data)
            self.assertNotIn('additional_prompt', event)
            self.assertEqual(network.call_args.args[0].get_header('Idempotency-key'),
                             'TTEST:C123:1792500001.000002:reply-v1:0')
            self.assertIs(event['reply_in_thread'], False)
            self.assertEqual(event['thread_ts'], '1792500001.000002')

    @patch('desk.slack.slack_api')
    @patch('desk.slack.urlopen')
    def test_reply_choice_reaches_bb_and_changes_retry_identity(self, network, api):
        network.side_effect = lambda *a, **kw: self.accepted()
        self.post()
        keys = {}
        for reply in (False, True, True, False, None):
            result = self.submit(api, 'Analyze only', reply=reply)
            request = network.call_args.args[0]
            choice = reply is True
            self.assertIs(json.loads(request.data)['reply_in_thread'], choice)
            key = request.get_header('Idempotency-key')
            if choice in keys:
                self.assertEqual(key, keys[choice])
            keys[choice] = key
            self.assertIn('may reply in the original Slack thread' if choice else
                          'will reply only in BB', json.dumps(result.json()))
        self.assertNotEqual(keys[True], keys[False])

    @patch('desk.slack.slack_api')
    @patch('desk.slack.urlopen')
    def test_invalid_reply_choice_does_not_dispatch(self, network, api):
        self.post()
        metadata = api.call_args.kwargs['view']['private_metadata']
        for selected in ('true', [None], [{'value': 'other'}], [{'value': 'reply'}] * 2):
            result = self.post(type='view_submission', view={
                'callback_id': 'revops_send_to_bb_submit', 'private_metadata': metadata,
                'state': {'values': {'reply_in_thread': {'input': {'selected_options': selected}}}},
            })
            self.assertEqual(result.json()['response_action'], 'errors')
            self.assertIn('reply_in_thread', result.json()['errors'])
        network.assert_not_called()

    @patch('desk.slack.slack_api')
    @patch('desk.slack.urlopen')
    def test_other_users_and_disabled_configuration_do_not_dispatch(self, network, api):
        self.assertEqual(self.post(user={'id': 'UOTHER'}).status_code, 200)
        network.assert_not_called()
        self.assertIn('restricted', json.dumps(api.call_args.kwargs['view']))
        with override_settings(BB_SLACK_USER_ID=''):
            self.assertEqual(self.post().status_code, 200)
        with override_settings(BB_WEBHOOK_TOKEN=''):
            self.assertEqual(self.post().status_code, 200)
        network.assert_not_called()

    @patch('desk.slack.slack_api')
    @patch('desk.slack.urlopen')
    def test_queue_failure_is_visible_and_retry_remains_safe(self, network, api):
        network.side_effect = URLError('unavailable')
        self.post()
        result = self.submit(api, 'Keep this prompt')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['response_action'], 'errors')
        self.assertIn('not confirmed', json.dumps(result.json()))
        self.assertNotIn('Queued', json.dumps(result.json()))

    @patch('desk.slack.slack_api', side_effect=URLError('expired_trigger_id'))
    @patch('desk.slack.urlopen')
    def test_failed_modal_open_does_not_dispatch(self, network, api):
        self.assertEqual(self.post().status_code, 503)
        network.assert_not_called()

    @patch('desk.slack.slack_api')
    @patch('desk.slack.urlopen')
    def test_submission_rechecks_access_and_signed_context(self, network, api):
        self.post()
        self.assertEqual(self.submit(api, user={'id': 'UOTHER'}).status_code, 403)
        with override_settings(BB_SLACK_USER_ID=''):
            self.assertEqual(self.submit(api).status_code, 403)
        with override_settings(BB_WEBHOOK_TOKEN=''):
            self.assertEqual(self.submit(api).json()['response_action'], 'errors')
        metadata = api.call_args.kwargs['view']['private_metadata']
        api.call_args.kwargs['view']['private_metadata'] = metadata + 'tampered'
        self.assertEqual(self.submit(api).status_code, 403)
        api.call_args.kwargs['view']['private_metadata'] = metadata
        with patch('django.core.signing.time.time', return_value=time.time() + 3601):
            self.assertEqual(self.submit(api).status_code, 403)
        self.assertEqual(self.submit(api, 'x' * 3001).json()['response_action'], 'errors')
        network.assert_not_called()

    @patch('desk.slack.slack_api')
    @patch('desk.slack.urlopen')
    def test_invalid_context_cannot_dispatch(self, network, api):
        for change in [
            {'team': {'id': 'TOTHER'}},
            {'api_app_id': 'AOTHER'},
            {'channel': {'id': 'https://attacker.invalid'}},
            {'message_ts': 'invalid'},
            {'message': {'ts': '1792500009.000009'}},
            {'message': {'ts': '1792500001.000002', 'thread_ts': 'invalid'}},
        ]:
            self.assertIn(self.post(**change).status_code, (400, 403))
        network.assert_not_called()
