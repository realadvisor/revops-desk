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

    @patch('desk.slack.slack_api')
    @patch('desk.slack.urlopen')
    def test_parent_and_reply_route_to_correct_thread_with_stable_retry_key(self, network, api):
        network.side_effect = lambda *a, **kw: self.accepted()
        self.assertEqual(self.post().status_code, 200)
        request = network.call_args.args[0]
        self.assertEqual(request.get_header('Idempotency-key'), 'TTEST:C123:1792500001.000002')
        self.assertEqual(request.get_header('Authorization'), 'Bearer bb-test-token')
        self.assertEqual(json.loads(request.data), {
            'type': 'slack_message_shortcut', 'team_id': 'TTEST', 'channel_id': 'C123',
            'message_ts': '1792500001.000002', 'thread_ts': '1792500001.000002',
            'requested_by': 'UTHEO',
        })
        self.assertIn('Queued', json.dumps(api.call_args.kwargs['view']))
        self.post(message={'ts': '1792500001.000002', 'text': 'Edited'})
        self.assertEqual(network.call_args.args[0].data, request.data)
        self.assertEqual(network.call_args.args[0].get_header('Idempotency-key'),
                         request.get_header('Idempotency-key'))
        self.post(message={'ts': '1792500001.000002', 'thread_ts': '1792500000.000001'})
        self.assertEqual(json.loads(network.call_args.args[0].data)['thread_ts'], '1792500000.000001')

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
        self.assertEqual(self.post().status_code, 200)
        self.assertIn('not confirmed', json.dumps(api.call_args.kwargs['view']))
        self.assertNotIn('Queued', json.dumps(api.call_args.kwargs['view']))

    @patch('desk.slack.slack_api', side_effect=URLError('expired_trigger_id'))
    @patch('desk.slack.urlopen')
    def test_confirmation_failure_does_not_reject_accepted_work(self, network, api):
        network.return_value = self.accepted()
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(network.call_count, 1)

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
