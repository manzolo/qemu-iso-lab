"""Real HTTP checks, executed on the client by the numbered Bash scripts.

requests follows the HTML login form, not the password grant. URL translation
stands in for the host's QEMU forwards when this browser runs inside the guest.
"""
import base64
import hashlib
from html.parser import HTMLParser
import json
import secrets
import sys
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import requests

ISSUER = 'http://127.0.0.1:18080/realms/lab'
APP = 'http://127.0.0.1:18081'
KC = 'http://172.20.20.1:8080'
OIDC = KC + '/realms/lab/protocol/openid-connect'


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print('OK:', message)


def internal(url):
    for public, private in [('http://127.0.0.1:18080', KC), (APP, 'http://172.20.20.2:8000')]:
        if url == public or url.startswith(public + '/'):
            return private + url[len(public):]
    # Only our two guests are permitted: a wrong redirect must never be followed externally.
    if urlsplit(url).hostname not in ['172.20.20.1', '172.20.20.2']:
        raise AssertionError('Unexpected redirect: ' + url)
    return url


def send(session, method, url, **kwargs):
    return session.request(method, internal(url), timeout=15, allow_redirects=False, **kwargs)


def follow(session, response):
    for _ in range(12):
        if response.status_code not in [301, 302, 303, 307, 308]:
            return response
        response = send(session, 'GET', urljoin(response.url, response.headers['Location']))
    raise AssertionError('redirect loop')


class Form(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.action = None
        self.fields = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form' and attrs.get('id') == 'kc-form-login':
            self.action = attrs.get('action')
        if tag == 'input' and attrs.get('name'):
            self.fields[attrs['name']] = attrs.get('value', '')


def credentials(session, response, user):
    if response.status_code == 200:
        form = Form(response.text)
        check(form.action is not None, 'Keycloak served its actual login form')
        data = {**form.fields, 'username': user, 'password': user + '-lab-pass'}
        response = send(session, 'POST', form.action, data=data)
    return response


def login(user='alice', session=None):
    session = session or requests.Session()
    response = send(session, 'GET', APP + '/login')
    check(response.status_code == 302, 'app redirects to the identity provider')
    query = parse_qs(urlsplit(response.headers['Location']).query)
    check(query['code_challenge_method'] == ['S256'] and len(query['code_challenge'][0]) == 43,
          'Authorization Code uses S256 PKCE')
    check(all(query.get(k) for k in ['state', 'nonce']), 'state and nonce are present')
    response = send(session, 'GET', response.headers['Location'])
    response = credentials(session, response, user)
    response = follow(session, response)
    check(response.status_code == 200 and response.json().get('user') == user,
          f'{user} completes code exchange and gets /protected 200 (received {response.status_code})')
    return session


def csrf(session):
    response = send(session, 'GET', APP + '/actions')
    check(response.status_code == 200, 'session can read CSRF-protected actions')
    return Form(response.text).fields['csrf']


def decode(token):
    # Inspection only. The app verifies with JWKS; this helper must not authorize anything.
    return json.loads(base64.urlsafe_b64decode(token.split('.')[1] + '==='))


def service(secret='service-lab-secret'):
    return requests.post(OIDC + '/token', data=dict(grant_type='client_credentials',
        client_id='lab-service', client_secret=secret), timeout=15)


def introspect(token):
    response = requests.post(OIDC + '/token/introspect', data=dict(token=token,
        client_id='lab-web', client_secret='web-lab-secret'), timeout=15)
    check(response.status_code == 200, 'introspection returns JSON to authenticated client')
    return response.json()


def raw_code():
    session = requests.Session()
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    response = send(session, 'GET', ISSUER + '/protocol/openid-connect/auth?' + urlencode(dict(
        client_id='lab-web', redirect_uri=APP + '/callback', response_type='code', scope='openid',
        state=secrets.token_urlsafe(24), nonce=secrets.token_urlsafe(24),
        code_challenge=challenge, code_challenge_method='S256')))
    response = credentials(session, response, 'alice')
    check(response.status_code == 302 and 'code=' in response.headers.get('Location', ''),
          'interactive login issues an authorization code')
    return parse_qs(urlsplit(response.headers['Location']).query)['code'][0], verifier


def exchange(code, verifier):
    return requests.post(OIDC + '/token', data=dict(grant_type='authorization_code',
        code=code, code_verifier=verifier, redirect_uri=APP + '/callback',
        client_id='lab-web', client_secret='web-lab-secret'), timeout=15)


def discovery():
    response = requests.get(KC + '/realms/lab/.well-known/openid-configuration', timeout=15)
    check(response.status_code == 200, 'discovery is reachable on the private segment')
    data = response.json()
    check(data['issuer'] == ISSUER, 'discovery has the configured canonical issuer')
    for key, suffix in [('authorization_endpoint', '/auth'), ('token_endpoint', '/token'),
                        ('jwks_uri', '/certs'), ('end_session_endpoint', '/logout')]:
        check(data[key] == ISSUER + '/protocol/openid-connect' + suffix, key)
    jwks = requests.get(OIDC + '/certs', timeout=15).json()['keys']
    response = service()
    check(response.status_code == 200, 'known service credentials return a token')
    token = response.json()['access_token']
    header = json.loads(base64.urlsafe_b64decode(token.split('.')[0] + '==='))
    check(any(k['kid'] == header['kid'] and k.get('use') == 'sig' for k in jwks), 'JWT kid exists in JWKS')
    claims = decode(token)
    check(claims['iss'] == ISSUER and 'lab-api' in claims['aud'] and claims['exp'] > time.time(),
          'iss, aud and exp match known values')
    check(requests.get('http://172.20.20.2:8000/api', headers={'Authorization':'Bearer '+token},
                       timeout=15).status_code == 200, 'app verifies the signature using JWKS')
    print(json.dumps(claims, indent=2))


def flow():
    session = requests.Session()
    check(send(session, 'GET', APP + '/protected').status_code == 401, 'anonymous access is 401')
    session = login(session=session)
    first = session.cookies.get('lab_session')
    token_view = send(session, 'GET', APP + '/tokens').json()
    check(token_view['id_claims']['aud'] == 'lab-web', 'ID token is for lab-web')
    check('editor' in token_view['access_claims']['realm_access']['roles'], 'Alice token contains editor')
    check(send(session, 'POST', APP + '/refresh', data={'csrf': 'wrong'}).status_code == 400,
          'CSRF rejects a forged refresh request')
    refreshed = follow(session, send(session, 'POST', APP + '/refresh', data={'csrf': csrf(session)}))
    check(refreshed.status_code == 200, 'app rotates tokens on refresh')
    # Second login reuses the Keycloak SSO cookie, creating a second local app session.
    session = login(session=session)
    second = session.cookies.get('lab_session')
    check(first != second, 'SSO creates a second independent app session')
    old_cookie = requests.Session()
    old_cookie.cookies.set('lab_session', first, domain='172.20.20.2', path='/')
    check(send(old_cookie, 'GET', APP + '/protected').status_code == 200, 'first session is still active')
    old_csrf = csrf(old_cookie)
    result = follow(session, send(session, 'POST', APP + '/logout', data={'csrf': csrf(session)}))
    check(result.status_code == 200 and 'id="home"' in result.text, 'RP logout returns home')
    check(send(session, 'GET', APP + '/protected').status_code == 401, 'current session logged out')
    check(send(old_cookie, 'POST', APP + '/refresh', data={'csrf': old_csrf}).status_code == 400,
          'backchannel removed the first local session before introspection')
    check(send(old_cookie, 'GET', APP + '/protected').status_code == 401, 'single sign-out invalidates first session too')
    response = send(session, 'GET', APP + '/login')
    response = send(session, 'GET', response.headers['Location'])
    check(response.status_code == 200 and Form(response.text).action, 'Keycloak asks for credentials again')


def roles():
    alice, bob = login('alice'), login('bob')
    check(send(alice, 'GET', APP + '/edit').status_code == 200, 'Alice with editor gets 200')
    response = send(bob, 'GET', APP + '/edit')
    check(response.status_code == 403 and response.json()['error'] == 'insufficient_role',
          'Bob without editor gets 403, not a login failure')
    check(send(bob, 'GET', APP + '/protected').status_code == 200, 'Bob is authenticated')
    for session in [alice, bob]:
        follow(session, send(session, 'POST', APP + '/logout', data={'csrf': csrf(session)}))


def tokens():
    response = service()
    check(response.status_code == 200, 'client credentials succeeds with known secret')
    token = response.json()['access_token']
    check('refresh_token' not in response.json(), 'service grant has no user refresh token')
    check('lab-api' in decode(token)['aud'], 'service token targets lab-api')
    check(introspect(token)['active'] is True, 'new token is active')
    code, verifier = raw_code()
    response = exchange(code, verifier)
    check(response.status_code == 200, 'code exchange returns user tokens')
    old = response.json()
    response = requests.post(OIDC + '/token', data=dict(grant_type='refresh_token',
        refresh_token=old['refresh_token'], client_id='lab-web', client_secret='web-lab-secret'), timeout=15)
    check(response.status_code == 200, 'refresh token obtains a new access token')
    new = response.json()
    check(new['refresh_token'] != old['refresh_token'], 'refresh token rotates')
    check(decode(new['access_token'])['sub'] == decode(old['access_token'])['sub'], 'refresh preserves subject')
    rejected = requests.post(OIDC + '/token', data=dict(grant_type='refresh_token',
        refresh_token=old['refresh_token'], client_id='lab-web', client_secret='web-lab-secret'), timeout=15)
    check(rejected.status_code == 400 and rejected.json()['error'] == 'invalid_grant', 'reused refresh token rejected')
    requests.post(OIDC + '/logout', data=dict(refresh_token=new['refresh_token'],
        client_id='lab-web', client_secret='web-lab-secret'), timeout=15).raise_for_status()
    check(introspect(new['access_token'])['active'] is False, 'logout revokes the SSO session')


def errors():
    response = requests.get(OIDC + '/auth', params=dict(client_id='lab-web',
        redirect_uri='http://not-allowed.invalid/callback', response_type='code', scope='openid'),
        timeout=15, allow_redirects=False)
    check(response.status_code == 400 and 'redirect_uri' in response.text,
          'unregistered redirect URI is rejected without redirecting')
    response = service('deliberately-wrong')
    check(response.status_code == 401 and response.json()['error'] == 'unauthorized_client', 'wrong secret gives unauthorized_client')
    code, verifier = raw_code()
    response = exchange(code, 'wrong-verifier-' + verifier)
    check(response.status_code == 400 and response.json()['error'] == 'invalid_grant', 'wrong PKCE verifier rejected')
    code, verifier = raw_code()
    check(exchange(code, verifier).status_code == 200, 'correct PKCE verifier accepted')
    response = exchange(code, verifier)
    check(response.status_code == 400 and response.json()['error'] == 'invalid_grant', 'authorization code is single-use')
    session = requests.Session()
    check(send(session, 'GET', APP + '/callback?code=fake&state=fake').status_code == 400, 'unsolicited callback rejected')
    token = service().json()['access_token']
    # Alter signed payload without re-signing it; leave the key id untouched.
    parts = token.split('.')
    claims = decode(token)
    claims['sub'] = 'attacker'
    parts[1] = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b'=').decode()
    response = send(session, 'GET', APP + '/api', headers={'Authorization': 'Bearer ' + '.'.join(parts)})
    check(response.status_code == 401 and response.json()['error'] == 'InvalidSignatureError', 'tampered JWT signature rejected')
    response = send(session, 'GET', APP + '/api', headers={'Authorization': 'Bearer ' + token})
    check(response.status_code == 200, 'same unmodified token works before expiration')
    wait = max(0, decode(token)['exp'] - time.time() + 1)
    check(wait <= 8, 'service token has deliberately short five-second life')
    time.sleep(wait)
    response = send(session, 'GET', APP + '/api', headers={'Authorization': 'Bearer ' + token})
    check(response.status_code == 401 and response.json()['error'] == 'ExpiredSignatureError', 'real expired token gives 401')
    # Keycloak compares exp with whole seconds on its own clock: measured 2026-10-20, a token still introspects as
    # active up to 0.9 s after exp, and the server's clock sat half a second behind the client's. Three seconds past
    # exp is past every edge; before, this check failed about two runs in three.
    time.sleep(max(0, decode(token)['exp'] + 3 - time.time()))
    check(introspect(token)['active'] is False, 'expired token introspects as inactive')


if __name__ == '__main__':
    {'discovery': discovery, 'flow': flow, 'roles': roles, 'tokens': tokens, 'errors': errors}[sys.argv[1]]()
