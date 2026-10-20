"""Small, inspectable OIDC teaching app. One worker: sessions live in memory.

Public URLs address the host browser; back-end HTTP crosses keycloak-lan.
Never derive the issuer or the back-end destination from an incoming request.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from html import escape
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


# --- Pages for the browser. curl and the tests ask for */* and keep getting the JSON below; a browser
# asks for text/html and gets the same answer as a page. Nothing here decides who may see what.
CSS = """
*{box-sizing:border-box}body{margin:0;font:19px/1.5 system-ui,-apple-system,"Segoe UI",Ubuntu,sans-serif;
background:#f2f4f8;color:#1d2433}header{background:#1d2433;color:#fff;padding:14px 28px;display:flex;
align-items:center;gap:22px}header b{font-size:20px;margin-right:auto}header a{color:#c9d3ea;text-decoration:none}
header a:hover{color:#fff}main{max-width:1240px;margin:28px auto;padding:0 20px}.card{background:#fff;
border-radius:10px;box-shadow:0 1px 3px rgba(20,30,60,.12);padding:22px 26px;margin-bottom:20px}
h1{font-size:30px;margin:0 0 8px}h2{font-size:21px;margin:0 0 12px}p{margin:6px 0}.muted{color:#5b6478}
.ok{border-left:6px solid #2e9d5b}.no{border-left:6px solid #d0413b}.who{border-left:6px solid #3a6fd8}
.chip{display:inline-block;background:#e6edfb;color:#24479a;border-radius:99px;padding:2px 12px;margin:2px 4px 2px 0;font-size:16px}
.btn{display:inline-block;background:#3a6fd8;color:#fff;border:0;border-radius:7px;padding:10px 20px;font:inherit;
cursor:pointer;text-decoration:none;margin:4px 8px 4px 0}.btn.alt{background:#fff;color:#3a6fd8;border:1px solid #3a6fd8}
.btn.red{background:#d0413b}code,pre,.jwt{font-family:"DejaVu Sans Mono",ui-monospace,monospace;font-size:16px}
.jwt{word-break:break-all;background:#1d2433;color:#eee;border-radius:8px;padding:14px 16px;line-height:1.45}
.h{color:#ff7b8a}.p{color:#c58cff}.s{color:#4fd1e8}.legend span{margin-right:18px;font-size:16px}
table{border-collapse:collapse;width:100%}td{padding:7px 10px;border-top:1px solid #e8ebf2;vertical-align:top}
td:first-child{font-family:"DejaVu Sans Mono",monospace;font-size:14px;white-space:nowrap;color:#24479a}
td:last-child{color:#5b6478;font-size:16px}tr.key td:nth-child(2){font-weight:600}
td:nth-child(2){font-family:"DejaVu Sans Mono",monospace;font-size:14px;word-break:break-all}
details summary{cursor:pointer;color:#3a6fd8}pre{background:#f6f7fb;border-radius:8px;padding:12px;overflow:auto}
"""

MEANING = {
    'iss': 'issuer: who signed it (the realm)', 'aud': 'audience: who it is for', 'azp': 'the app that asked for it',
    'sub': "the user's id in the realm", 'preferred_username': 'user name', 'realm_access': 'roles in the realm',
    'iat': 'issued at', 'exp': 'valid until', 'auth_time': 'when the user typed the password', 'scope': 'what was asked',
    'sid': 'Keycloak session (single sign-out)', 'nonce': 'ties the ID token to this login', 'typ': 'kind of token',
    'jti': 'unique id of this token', 'email': 'e-mail', 'name': 'full name', 'alg': 'signature algorithm',
    'kid': 'which key of Keycloak signed it (from the JWKS)',
}
KEY_CLAIMS = ['iss', 'aud', 'azp', 'sub', 'preferred_username', 'realm_access', 'iat', 'exp']


def wants_html():
    return 'text/html' in request.headers.get('Accept', '')


def page(title, body, status=200):
    record = sessions.get(request.cookies.get('lab_session'))
    who = '<a href="/actions">Session / log out</a>' if record else ''
    return (f'<!doctype html><html lang="en"><meta charset="utf-8"><title>{escape(title)} · Keycloak lab</title>'
            f'<style>{CSS}</style><header><b>Keycloak lab · app lab-web</b><a href="/">Home</a>'
            f'<a href="/protected">Protected</a><a href="/edit">Editor</a><a href="/tokens">Tokens</a>{who}</header>'
            f'<main>{body}</main></html>'), status


def when(seconds):
    moment = datetime.fromtimestamp(seconds, timezone.utc).strftime('%H:%M:%S UTC')
    left = int(seconds - time.time())
    return f'{moment} (in {left} s)' if left >= 0 else f'{moment} ({-left} s ago)'


def value(name, raw):
    if name in ('iat', 'exp', 'auth_time') and isinstance(raw, int):
        return escape(when(raw))
    if name == 'realm_access':
        return ''.join(f'<span class="chip">{escape(r)}</span>' for r in raw.get('roles', []))
    if isinstance(raw, list):
        return ''.join(f'<span class="chip">{escape(str(r))}</span>' for r in raw)
    return escape(json.dumps(raw) if isinstance(raw, dict) else str(raw))


def claims_table(claims, everything=False):
    names = [n for n in KEY_CLAIMS if n in claims]
    if everything:
        names += sorted(n for n in claims if n not in names)
    rows = ''.join(f'<tr class="{"key" if n in KEY_CLAIMS else ""}"><td>{escape(n)}</td><td>{value(n, claims[n])}</td>'
                   f'<td>{escape(MEANING.get(n, ""))}</td></tr>' for n in names)
    return f'<table>{rows}</table>'


@app.get('/')
def index():
    record = sessions.get(request.cookies.get('lab_session'))
    state = ('<p class="muted">You are signed in. <a href="/protected">Go to the protected page</a>.</p>' if record else
             '<p class="muted">Nobody is signed in. This app has no password form of its own:</p>'
             '<p><a class="btn" href="/login">Login</a> <span class="muted">you will sign in on Keycloak, then come back here.</span></p>')
    return page('Home', '<div class="card who" id="home"><h1>A small app protected by Keycloak</h1>'
                '<p>OpenID Connect, Authorization Code with PKCE. The app never sees your password: it receives a signed '
                f'token from Keycloak and checks it.</p>{state}</div>')


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
    html = wants_html()
    if record is None:
        if html:
            return page('Login required', '<div class="card no"><h1>401 · who are you?</h1><p>This page needs a login, '
                        'and nobody is signed in.</p><p><a class="btn" href="/login">Login</a></p></div>', 401)
        return jsonify(error='login_required', login='/login'), 401
    roles = claims.get('realm_access', {}).get('roles', [])
    chips = ''.join(f'<span class="chip">{escape(r)}</span>' for r in roles) or '<span class="muted">none</span>'
    user = escape(claims['preferred_username'])
    if request.path == '/edit' and 'editor' not in roles:
        if html:
            return page('Forbidden', f'<div class="card no"><h1>403 · forbidden</h1><p>Keycloak knows who you are: '
                        f'<b>{user}</b>. But this page wants the role <span class="chip">editor</span>, and your roles '
                        f'are: {chips}</p><p class="muted">Authentication said who you are; authorization says what you '
                        'may do.</p><p class="muted"><code>insufficient_role</code></p></div>', 403)
        return jsonify(error='insufficient_role', required='editor'), 403
    if request.path == '/tokens':
        # Show verified claims, never expose refresh tokens to browser JavaScript.
        identity = verify(record['tokens']['id_token'], CLIENT)
        if not html:
            return jsonify(access_claims=claims, id_claims=identity)
        token = record['tokens']['access_token']
        head, body, sig = (escape(part) for part in token.split('.'))
        header = jwt.get_unverified_header(token)
        return page('Tokens', '<div class="card who"><h1>The access token of ' + user + '</h1>'
                    '<p class="muted">Three pieces separated by a dot: header, payload, signature. The first two are '
                    'only base64, anyone can read them; the signature is what the app checks with Keycloak\'s key.</p>'
                    f'<div class="jwt"><span class="h">{head}</span>.<span class="p">{body}</span>.<span class="s">{sig}</span></div>'
                    '<p class="legend"><span class="h">■ header</span><span class="p">■ payload</span>'
                    '<span class="s">■ signature</span></p></div>'
                    '<div class="card ok"><h2>Header</h2>' + claims_table(header, True) +
                    '</div><div class="card ok"><h2>Payload: the claims, checked</h2><p class="muted">Signature, issuer, '
                    'audience and expiry already verified by the app.</p>' + claims_table(claims) +
                    '<details><summary>all the claims of the access token</summary>' + claims_table(claims, True) +
                    '</details></div><div class="card"><h2>ID token</h2><p class="muted">For the app itself '
                    '(audience lab-web): who logged in.</p>' + claims_table(identity) + '</div>')
    if request.path == '/actions':
        csrf = record['csrf']
        forms = (f'<form method="post" action="/refresh"><input type="hidden" name="csrf" value="{csrf}">'
                 '<button class="btn alt">Refresh token</button></form>'
                 f'<form method="post" action="/logout"><input type="hidden" name="csrf" value="{csrf}">'
                 '<button class="btn red">Logout everywhere in this SSO session</button></form>')
        if not html:
            return forms
        return page('Session', f'<div class="card who"><h1>Session of {user}</h1><p>Token valid until '
                    f'{escape(when(claims["exp"]))}.</p><p class="muted">Refresh asks Keycloak for new tokens with the '
                    'refresh token. Logout goes through Keycloak, which closes the session for every app and sends '
                    'you back here.</p>' + forms.replace('</form><form', '</form> <form') + '</div>')
    if html:
        title = 'Editor page' if request.path == '/edit' else 'Protected page'
        what = ('You have the role <span class="chip">editor</span>: this page is yours.' if request.path == '/edit'
                else 'You are signed in: Keycloak said who you are, and the app checked its signature.')
        return page(title, f'<div class="card ok"><h1>{title} · hello, {user}</h1><p>{what}</p><p>Your roles: {chips}</p>'
                    f'<p class="muted">Token valid until {escape(when(claims["exp"]))}. '
                    '<a href="/tokens">See what is inside the token</a>.</p></div>')
    return jsonify(user=claims['preferred_username'], roles=roles,
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
