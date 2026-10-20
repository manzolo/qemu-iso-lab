"""Small, inspectable OIDC teaching app. One worker: sessions live in memory.

Public URLs address the host browser; back-end HTTP crosses keycloak-lan.
Never derive the issuer or the back-end destination from an incoming request.
"""
import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlencode

import jwt
import requests
from flask import Flask, jsonify, redirect, request

ISSUER = 'http://127.0.0.1:18080/realms/lab'
INTERNAL = 'http://172.20.20.1:8080/realms/lab'
APP_URL = 'http://127.0.0.1:18081'
CLIENT = 'lab-web'
SECRET = 'web-lab-secret'  # Public example credential, for this isolated lab only.
PROTOCOL = INTERNAL + '/protocol/openid-connect'
PUBLIC_PROTOCOL = ISSUER + '/protocol/openid-connect'
app = Flask(__name__)
sessions = {}
pending = {}
logout_ids = {}
keys = jwt.PyJWKClient(PROTOCOL + '/certs', timeout=10)


def token_request(data):
    response = requests.post(PROTOCOL + '/token', data={
        'client_id': CLIENT, 'client_secret': SECRET, **data}, timeout=10)
    response.raise_for_status()
    return response.json()


def verify(token, audience, required=('exp', 'iat', 'iss', 'aud', 'sub')):
    key = keys.get_signing_key_from_jwt(token).key
    return jwt.decode(token, key, algorithms=['RS256'], audience=audience,
                      issuer=ISSUER, options={'require': list(required)})


def active(token):
    response = requests.post(PROTOCOL + '/token/introspect', data={
        'client_id': CLIENT, 'client_secret': SECRET, 'token': token}, timeout=10)
    response.raise_for_status()
    return response.json().get('active') is True


def prune():
    now = time.time()
    for collection in (pending, sessions):
        for sid, value in list(collection.items()):
            if value['deadline'] < now:
                collection.pop(sid, None)
    for jti, expiry in list(logout_ids.items()):
        if expiry < now:
            logout_ids.pop(jti, None)


@app.before_request
def expire_memory():
    prune()


@app.after_request
def no_cache(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.get('/')
def index():
    return ('<h1>Keycloak lab</h1><p>Authorization Code + S256 PKCE</p>'
            '<a href="/login">Login</a> | <a href="/protected">Protected</a> | '
            '<a href="/edit">Editor</a> | <a href="/tokens">Read token claims</a> | '
            '<a href="/actions">Refresh / logout</a>')


@app.get('/login')
def login():
    sid, state, nonce, verifier = [secrets.token_urlsafe(32) for _ in range(4)]
    pending[sid] = dict(state=state, nonce=nonce, verifier=verifier,
                        deadline=time.time() + 300)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    response = redirect(PUBLIC_PROTOCOL + '/auth?' + urlencode(dict(
        client_id=CLIENT, redirect_uri=APP_URL + '/callback', response_type='code',
        scope='openid profile email', state=state, nonce=nonce,
        code_challenge=challenge, code_challenge_method='S256')))
    response.set_cookie('lab_login', sid, httponly=True, samesite='Lax', max_age=300)
    return response


@app.get('/callback')
def callback():
    flow = pending.pop(request.cookies.get('lab_login'), None)
    if not flow or not hmac.compare_digest(flow['state'], request.args.get('state', '')):
        return jsonify(error='invalid_state'), 400
    if 'code' not in request.args:
        return jsonify(error=request.args.get('error', 'missing_code')), 400
    tokens = token_request(dict(grant_type='authorization_code', code=request.args['code'],
                               redirect_uri=APP_URL + '/callback', code_verifier=flow['verifier']))
    identity = verify(tokens['id_token'], CLIENT)
    if not hmac.compare_digest(identity.get('nonce', ''), flow['nonce']):
        return jsonify(error='invalid_nonce'), 400
    claims = verify(tokens['access_token'], 'lab-api')
    sid = secrets.token_urlsafe(32)
    sessions[sid] = dict(tokens=tokens, sub=claims['sub'], oidc_sid=identity.get('sid'),
                         csrf=secrets.token_urlsafe(32), deadline=time.time() + 1800)
    response = redirect('/protected')
    response.delete_cookie('lab_login')
    response.set_cookie('lab_session', sid, httponly=True, samesite='Lax', max_age=1800)
    return response


def current():
    record = sessions.get(request.cookies.get('lab_session'))
    if not record:
        return None, None
    token = record['tokens']['access_token']
    claims = verify(token, 'lab-api')
    # Online introspection demonstrates immediate SSO revocation. Offline JWT validation
    # alone would accept a correctly signed token until exp, even after logout.
    if not active(token):
        sessions.pop(request.cookies.get('lab_session'), None)
        return None, None
    return record, claims


@app.get('/protected')
@app.get('/edit')
@app.get('/tokens')
@app.get('/actions')
def protected():
    record, claims = current()
    if record is None:
        return jsonify(error='login_required', login='/login'), 401
    if request.path == '/edit' and 'editor' not in claims.get('realm_access', {}).get('roles', []):
        return jsonify(error='insufficient_role', required='editor'), 403
    if request.path == '/tokens':
        # Show verified claims, never expose refresh tokens to browser JavaScript.
        return jsonify(access_claims=claims, id_claims=verify(record['tokens']['id_token'], CLIENT))
    if request.path == '/actions':
        csrf = record['csrf']
        return (f'<form method="post" action="/refresh"><input type="hidden" name="csrf" value="{csrf}">'
                '<button>Refresh token</button></form>'
                f'<form method="post" action="/logout"><input type="hidden" name="csrf" value="{csrf}">'
                '<button>Logout everywhere in this SSO session</button></form>')
    return jsonify(user=claims['preferred_username'], roles=claims.get('realm_access', {}).get('roles', []),
                   page=request.path, expires=claims['exp'])


def csrf_record():
    record = sessions.get(request.cookies.get('lab_session'))
    if record and hmac.compare_digest(record['csrf'], request.form.get('csrf', '')):
        return record
    return None


@app.post('/refresh')
def refresh():
    # Refresh still works after the access token expired; the refresh token has its own life.
    record = csrf_record()
    if not record:
        return jsonify(error='invalid_csrf'), 400
    tokens = token_request(dict(grant_type='refresh_token', refresh_token=record['tokens']['refresh_token']))
    verify(tokens['access_token'], 'lab-api')
    identity = verify(tokens['id_token'], CLIENT)
    if identity['sub'] != record['sub']:
        return jsonify(error='subject_changed'), 400
    record['tokens'] = tokens
    return redirect('/protected')


@app.post('/logout')
def logout():
    record = csrf_record()
    if not record:
        return jsonify(error='invalid_csrf'), 400
    sessions.pop(request.cookies.get('lab_session'), None)
    response = redirect(PUBLIC_PROTOCOL + '/logout?' + urlencode(dict(
        id_token_hint=record['tokens']['id_token'], post_logout_redirect_uri=APP_URL + '/')))
    response.delete_cookie('lab_session')
    return response


@app.post('/backchannel-logout')
def backchannel_logout():
    token = request.form.get('logout_token', '')
    claims = verify(token, CLIENT, required=('iat', 'iss', 'aud', 'jti'))
    event = 'http://schemas.openid.net/event/backchannel-logout'
    if (event not in claims.get('events', {}) or 'nonce' in claims
            or not (claims.get('sid') or claims.get('sub'))
            or abs(time.time() - claims['iat']) > 120 or claims['jti'] in logout_ids):
        return jsonify(error='invalid_logout_token'), 400
    logout_ids[claims['jti']] = time.time() + 180
    for sid, record in list(sessions.items()):
        matches = (record['oidc_sid'] == claims['sid'] if claims.get('sid')
                   else record['sub'] == claims['sub'])
        if matches:
            sessions.pop(sid, None)
    return '', 204


@app.get('/api')
def api():
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer '):
        return jsonify(error='bearer_required'), 401
    claims = verify(header[7:], 'lab-api')
    if not active(header[7:]):
        return jsonify(error='inactive_token'), 401
    return jsonify(audience=claims['aud'], subject=claims['sub'])


@app.errorhandler(jwt.PyJWTError)
def bad_token(error):
    return jsonify(error=type(error).__name__), 401


@app.errorhandler(requests.RequestException)
def unavailable(error):
    # Token endpoint errors are useful in a lab; never return token request bodies.
    if error.response is not None and error.response.status_code < 500:
        return jsonify(error='token_endpoint_rejected', detail=error.response.json().get('error')), 401
    return jsonify(error='identity_provider_unavailable'), 503
