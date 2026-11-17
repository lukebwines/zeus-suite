"""ZEUS private first-party sessions; independent of the public QR-reading server."""
import hmac
import hashlib
import secrets
import threading
import time
from http.cookies import SimpleCookie

COOKIE='zeus_session'
IDLE_SECONDS=8*3600
ABSOLUTE_SECONDS=24*3600
_guard=threading.RLock()
_sessions={}
_failures={}

def _digest(value):
    return hashlib.sha256(value.encode('ascii')).hexdigest()

def login(user,password,expected_user,expected_password,source='unknown'):
    now=time.time()
    source=str(source)[:80]
    with _guard:
        failures=[x for x in _failures.get(source,[]) if now-x<300]
        _failures[source]=failures
        if len(failures)>=10:
            return None,'Riprova fra alcuni minuti.'
        good_user=hmac.compare_digest(user.encode('utf-8'),expected_user.encode('utf-8'))
        good_password=hmac.compare_digest(password.encode('utf-8'),expected_password.encode('utf-8'))
        if not (good_user and good_password):
            failures.append(now)
            return None,'Credenziali non valide.'
        _failures.pop(source,None)
        token=secrets.token_urlsafe(36)
        key=_digest(token)
        _sessions[key]=(now,now)
        if len(_sessions)>200:
            for sid,(created,seen) in tuple(_sessions.items()):
                if now-seen>IDLE_SECONDS or now-created>ABSOLUTE_SECONDS:
                    _sessions.pop(sid,None)
        return token,None

def _cookie_value(header):
    if not header or len(header)>4096:return ''
    cookie=SimpleCookie()
    try:cookie.load(header)
    except Exception:return ''
    morsel=cookie.get(COOKIE)
    value=morsel.value if morsel else ''
    return value if 40<=len(value)<128 and all(c.isascii() and (c.isalnum() or c in '_-') for c in value) else ''

def valid(header, refresh=True):
    token=_cookie_value(header)
    if not token:return False
    now=time.time()
    key=_digest(token)
    with _guard:
        values=_sessions.get(key)
        if not values:return False
        created,seen=values
        if now-seen>IDLE_SECONDS or now-created>ABSOLUTE_SECONDS:
            _sessions.pop(key,None)
            return False
        if refresh:_sessions[key]=(created,now)
        return True

def logout(header):
    token=_cookie_value(header)
    if token:
        with _guard:_sessions.pop(_digest(token),None)

def cookie(token,secure=True):
    return f'{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={ABSOLUTE_SECONDS}' + ('; Secure' if secure else '')

def clear_cookie(secure=True):
    return f'{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0' + ('; Secure' if secure else '')
