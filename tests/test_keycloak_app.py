"""OIDC app trust boundaries, with real RSA signatures and no network/VM access.

Also run inside the provisioned client by exercise 4. Minimal host/CI Python may
lack the app's guest dependencies; only these tests are skipped in that case.
"""
import importlib.util
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

try:
    import flask
    import jwt
    import requests
    from cryptography.hazmat.primitives.asymmetric import rsa
    HAS_APP_DEPS = True
except ImportError:
    HAS_APP_DEPS = False

APP_PATH = Path(__file__).resolve().parents[1] / 'vms/labs/keycloak-lab/provision/app.py'


@unittest.skipUnless(HAS_APP_DEPS, 'Flask/PyJWT/cryptography/requests are guest dependencies')
class KeycloakAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        spec = importlib.util.spec_from_file_location('keycloak_lab_app', APP_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.client = self.module.app.test_client()
        signing = mock.patch.object(self.module.keys, 'get_signing_key_from_jwt',
                                    return_value=mock.Mock(key=self.private.public_key()))
        signing.start()
        self.addCleanup(signing.stop)
        # Any unplanned network access fails; the suite must be independent of a running lab.
        network = mock.patch.object(self.module.requests, 'post', side_effect=AssertionError('unexpected HTTP'))
        self.http = network.start()
        self.addCleanup(network.stop)

    def claims(self, **changes):
        now = int(time.time())
        return dict(dict(iss=self.module.ISSUER, aud='lab-api', sub='alice',
                         iat=now, exp=now + 120, preferred_username='alice',
                         realm_access={'roles': ['editor']}), **changes)

    def signed(self, claims):
        return jwt.encode(claims, self.private, algorithm='RS256', headers={'kid': 'test-key'})

    def test_api_checks_signature_issuer_audience_expiry_before_introspection(self):
        token = self.signed(self.claims())
        with mock.patch.object(self.module, 'active', return_value=True) as active:
            self.assertEqual(self.client.get('/api', headers={'Authorization': 'Bearer ' + token}).status_code, 200)
            for changes, error in [({'iss': 'http://wrong.invalid'}, 'InvalidIssuerError'),
                                   ({'aud': 'other-api'}, 'InvalidAudienceError'),
                                   ({'exp': int(time.time()) - 1}, 'ExpiredSignatureError')]:
                active.reset_mock()
                response = self.client.get('/api', headers={'Authorization': 'Bearer ' + self.signed(self.claims(**changes))})
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json['error'], error)
                active.assert_not_called()
            missing_exp = self.claims()
            del missing_exp['exp']
            response = self.client.get('/api', headers={'Authorization': 'Bearer ' + self.signed(missing_exp)})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json['error'], 'MissingRequiredClaimError')

    def test_bad_state_is_rejected_before_exchanging_code(self):
        self.client.get('/login')
        response = self.client.get('/callback?state=wrong&code=stolen')
        self.assertEqual((response.status_code, response.json['error']), (400, 'invalid_state'))
        self.assertFalse(self.module.sessions)
        self.http.assert_not_called()

    def test_signed_id_token_with_wrong_nonce_cannot_create_session(self):
        self.client.get('/login')
        flow = next(iter(self.module.pending.values()))
        tokens = {'id_token': self.signed(self.claims(aud='lab-web', nonce='wrong')),
                  'access_token': self.signed(self.claims())}
        with mock.patch.object(self.module, 'token_request', return_value=tokens):
            response = self.client.get('/callback', query_string={'state': flow['state'], 'code': 'code'})
        self.assertEqual((response.status_code, response.json['error']), (400, 'invalid_nonce'))
        self.assertFalse(self.module.sessions)

    def test_valid_callback_consumes_state_and_stores_only_opaque_cookie(self):
        self.client.get('/login')
        flow = next(iter(self.module.pending.values()))
        tokens = {'id_token': self.signed(self.claims(aud='lab-web', nonce=flow['nonce'], sid='sso-1')),
                  'access_token': self.signed(self.claims()), 'refresh_token': 'private-refresh'}
        with mock.patch.object(self.module, 'token_request', return_value=tokens) as exchange:
            response = self.client.get('/callback', query_string={'state': flow['state'], 'code': 'code'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(exchange.call_args.args[0]['code_verifier'], flow['verifier'])
        cookies = ' '.join(response.headers.getlist('Set-Cookie'))
        self.assertIn('HttpOnly', cookies)
        self.assertIn('SameSite=Lax', cookies)
        self.assertNotIn('private-refresh', cookies)
        self.assertNotIn(tokens['access_token'], cookies)
        self.assertEqual(len(self.module.sessions), 1)
        response = self.client.get('/callback', query_string={'state': flow['state'], 'code': 'code'})
        self.assertEqual(response.status_code, 400)

    def test_backchannel_removes_only_matching_sessions_and_rejects_replay(self):
        for local, oidc in [('a', 'sso-1'), ('b', 'sso-1'), ('c', 'sso-2')]:
            self.module.sessions[local] = dict(oidc_sid=oidc, sub='alice', deadline=time.time() + 600)
        claims = self.claims(aud='lab-web', jti='logout-1', sid='sso-1',
                            events={'http://schemas.openid.net/event/backchannel-logout': {}})
        token = self.signed(claims)
        response = self.client.post('/backchannel-logout', data={'logout_token': token})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(set(self.module.sessions), {'c'})
        response = self.client.post('/backchannel-logout', data={'logout_token': token})
        self.assertEqual(response.status_code, 400)
        self.http.assert_not_called()

    def test_backchannel_rejects_nonce_missing_event_and_stale_iat(self):
        base = self.claims(aud='lab-web', jti='logout-1', sid='sso-1',
                           events={'http://schemas.openid.net/event/backchannel-logout': {}})
        for changes in [{'nonce': 'not-allowed'}, {'events': {}}, {'iat': int(time.time()) - 300}]:
            self.module.sessions['keep'] = dict(oidc_sid='sso-1', sub='alice', deadline=time.time() + 600)
            response = self.client.post('/backchannel-logout', data={'logout_token': self.signed(dict(base, **changes))})
            self.assertEqual(response.status_code, 400)
            self.assertIn('keep', self.module.sessions)

    def test_csrf_failure_does_not_logout_or_refresh(self):
        self.assertEqual(self.client.post('/logout', data={'csrf': 'fake'}).status_code, 400)
        self.assertEqual(self.client.post('/refresh', data={'csrf': 'fake'}).status_code, 400)
        self.http.assert_not_called()


if __name__ == '__main__':
    # Guest invocation: python3 - /opt/keycloak-lab/app.py < tests/test_keycloak_app.py
    if len(sys.argv) > 1 and sys.argv[1].endswith('.py'):
        if not HAS_APP_DEPS:
            raise SystemExit('Missing required guest app dependencies')
        APP_PATH = Path(sys.argv.pop(1))
    unittest.main()
