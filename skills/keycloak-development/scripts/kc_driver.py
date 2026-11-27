"""Keycloak REST and login flows from a pysess session. Usage: see keycloak-development SKILL.md.

api() prints the API: signatures and docstring summaries; help(name) shows the details.

Stdlib only. The module is loaded into the session's globals, so variables the caller defines
(json = r.json(), time = ...) would shadow plain module imports: every import is aliased.

Checks (run after editing):
    uvx ruff format --line-length 100 kc_driver.py
    uvx ruff check --line-length 100 --select E,W,F,I,UP,B,SIM kc_driver.py
    uvx ty check kc_driver.py
"""

from __future__ import annotations

import base64 as _base64
import hashlib as _hashlib
import hmac as _hmac
import inspect as _inspect
import json as _json
import re as _re
import secrets as _secrets
import socket as _socket
import ssl as _ssl
import struct as _struct
import threading as _threading
import time as _time
import urllib.error as _urlerror
import urllib.parse as _urlparse
import urllib.request as _urlrequest
from collections.abc import Callable, Sequence
from email.message import Message as _Message
from html.parser import HTMLParser as _HTMLParser
from http.client import HTTPConnection as _HTTPConnection
from http.client import HTTPSConnection as _HTTPSConnection
from http.cookiejar import CookieJar as _CookieJar
from typing import Any, NamedTuple

_PREVIEW = 300


class HTTPError(Exception):
    """Raised by Response.expect() and Response.raise_for_status()."""

    def __init__(self, message: str, response: Response) -> None:
        super().__init__(message)
        self.response = response


class Response:
    """HTTP response like requests.Response; never raised on 4xx/5xx.

    Attributes: method, url, status_code, headers, content. r["key"] is r.json()["key"].
    repr: one line; print(r): the whole body.
    """

    def __init__(
        self, method: str, url: str, status: int, headers: _Message, content: bytes
    ) -> None:
        self.method, self.url, self.status_code = method, url, status
        self.headers, self.content = headers, content
        self._json: Any = None
        self._parsed = False

    @property
    def ok(self) -> bool:
        """status_code < 400."""
        return self.status_code < 400

    @property
    def text(self) -> str:
        """Body as text (UTF-8)."""
        return self.content.decode("utf-8", "replace")

    def json(self) -> Any:
        """Parsed body; ValueError if not JSON."""
        if not self._parsed:
            self._json, self._parsed = _json.loads(self.content), True
        return self._json

    def __getitem__(self, key: Any) -> Any:
        return self.json()[key]

    @property
    def location(self) -> str | None:
        """The Location header."""
        return self.headers.get("Location")

    @property
    def id(self) -> str | None:
        """Last path segment of Location: the id after a 201."""
        loc = self.location
        return loc.rstrip("/").rsplit("/", 1)[-1] if loc else None

    def expect(self, *codes: int) -> Response:
        """Return self if status is in codes (default 2xx), else raise HTTPError."""
        good = self.status_code in codes if codes else 200 <= self.status_code < 300
        if not good:
            want = ", ".join(map(str, codes)) or "2xx"
            raise HTTPError(f"expected {want}, got {self!r}", self)
        return self

    def raise_for_status(self) -> None:
        """Raise HTTPError if status_code >= 400."""
        if not self.ok:
            raise HTTPError(repr(self), self)

    def _body_preview(self) -> str:
        try:
            body = self.json()
        except ValueError:
            return " ".join(self.text.split())[:_PREVIEW]
        if isinstance(body, list):
            return f"[{len(body)} items]"
        s = _json.dumps(body)
        return s if len(s) <= _PREVIEW else s[:_PREVIEW] + "..."

    def __repr__(self) -> str:
        path = _urlparse.urlsplit(self.url).path
        parts = [f"<{self.status_code} {self.method} {path}"]
        if self.id and self.status_code == 201:
            parts.append(f"id={self.id}")
        elif self.location:
            parts.append(f"-> {self.location}")
        if self.content:
            parts.append(self._body_preview())
        return " ".join(parts) + ">"

    def __str__(self) -> str:
        if not self.content:
            return repr(self)
        try:
            return _json.dumps(self.json(), indent=2)
        except ValueError:
            return self.text


class _NoRedirect(_urlrequest.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _encode_params(params: dict[str, Any] | None) -> str:
    if not params:
        return ""
    items = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in params.items()}
    return _urlparse.urlencode(items, doseq=True)


def _connect_to(target: tuple[str, int], context: _ssl.SSLContext) -> list[_urlrequest.BaseHandler]:
    """urllib handlers that send the same request (Host, SNI, certificate check), but open
    the TCP connection to target instead of the URL's host (like curl --connect-to)."""

    def create(address: Any, *args: Any, **kw: Any) -> _socket.socket:
        return _socket.create_connection(target, *args, **kw)

    class Conn(_HTTPConnection):
        _create_connection: Callable[..., _socket.socket]

        def __init__(self, *args: Any, **kw: Any) -> None:
            super().__init__(*args, **kw)
            self._create_connection = create

    class TlsConn(_HTTPSConnection):
        _create_connection: Callable[..., _socket.socket]

        def __init__(self, *args: Any, **kw: Any) -> None:
            super().__init__(*args, **kw)
            self._create_connection = create

    class Http(_urlrequest.HTTPHandler):
        def http_open(self, req: _urlrequest.Request) -> Any:
            return self.do_open(Conn, req)

    class Https(_urlrequest.HTTPSHandler):
        def https_open(self, req: _urlrequest.Request) -> Any:
            return self.do_open(TlsConn, req, context=context)

    return [Http(), Https(context=context)]


def _send(
    opener: _urlrequest.OpenerDirector,
    method: str,
    url: str,
    body: bytes | None,
    headers: dict[str, str],
    timeout: float,
) -> Response:
    req = _urlrequest.Request(url, data=body, method=method, headers=headers)
    try:
        with opener.open(req, timeout=timeout) as r:
            return Response(method, r.url, r.status, r.headers, r.read())
    except _urlerror.HTTPError as e:
        return Response(method, url, e.code, e.headers, e.read())


class Keycloak:
    """Keycloak REST client, like requests: kc.get/post/put/delete(path, ...).

    Paths under /admin/ get the admin token automatically (cached, refreshed before expiry
    and once on 401). token= overrides it for any path: a token string, a token-endpoint
    Response (its access_token is used), or "" for no Authorization header.

    headers: sent with every request (per-request headers win), like requests.Session.headers.
    connect_to: "host:port" to open TCP connections to instead of the host in url, like curl
    --connect-to. url stays the public URL (Host, issuer, cookies), so one node of a cluster
    can be targeted: Keycloak("http://lb:8080", connect_to="10.0.0.2:8080").

    Attributes: url, headers, connect_to, timeout, admin, admin_realm.
    """

    def __init__(
        self,
        url: str = "http://keycloak.127-0-0-1.nip.io:8080",
        admin: tuple[str, str] = ("admin", "admin"),
        admin_realm: str = "master",
        admin_client: str = "admin-cli",
        timeout: float = 30,
        verify: bool = True,
        headers: dict[str, str] | None = None,
        connect_to: str | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.headers = dict(headers or {})
        self.connect_to = connect_to
        self.admin, self.admin_realm, self.admin_client = admin, admin_realm, admin_client
        self.timeout = timeout
        self.ssl = _ssl.create_default_context()
        if not verify:
            self.ssl.check_hostname = False
            self.ssl.verify_mode = _ssl.CERT_NONE
        self._follow = _urlrequest.build_opener(*self.handlers())
        self._nofollow = _urlrequest.build_opener(*self.handlers(), _NoRedirect)
        self._admin_token: str | None = None
        self._admin_exp = 0.0
        self._lock = _threading.Lock()

    def __repr__(self) -> str:
        via = f", connect_to={self.connect_to!r}" if self.connect_to else ""
        return f"Keycloak({self.url!r}{via}, admin={self.admin[0]!r}@{self.admin_realm})"

    def handlers(self) -> list[_urlrequest.BaseHandler]:
        """urllib handlers for this node (TLS, connect_to)."""
        if not self.connect_to:
            return [_urlrequest.HTTPSHandler(context=self.ssl)]
        host, _, port = self.connect_to.rpartition(":")
        return _connect_to((host.strip("[]"), int(port)), self.ssl)

    def abs_url(self, path: str) -> str:
        """Full URL of a path relative to url."""
        return path if "://" in path else self.url + "/" + path.lstrip("/")

    def admin_token(self, refresh: bool = False) -> str:
        """Admin access token (cached until 10 s before exp)."""
        with self._lock:
            if refresh or not self._admin_token or _time.time() > self._admin_exp - 10:
                r = self.post(
                    f"/realms/{self.admin_realm}/protocol/openid-connect/token",
                    data={
                        "grant_type": "password",
                        "client_id": self.admin_client,
                        "username": self.admin[0],
                        "password": self.admin[1],
                    },
                ).expect(200)
                self._admin_token = r["access_token"]
                self._admin_exp = decode(self._admin_token).claims.get("exp", 0)
            assert self._admin_token is not None
            return self._admin_token

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | str | bytes | None = None,
        json: Any = None,
        *,
        headers: dict[str, str] | None = None,
        token: str | Response | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
    ) -> Response:
        """Send a request. data: dict=form, str=text/plain, bytes=raw; json: JSON body."""
        url = self.abs_url(path)
        if params:
            url += ("&" if "?" in url else "?") + _encode_params(params)
        hdrs = {"Accept": "application/json, */*;q=0.1"}
        body: bytes | None = None
        if json is not None:
            body, hdrs["Content-Type"] = _json.dumps(json).encode(), "application/json"
        elif isinstance(data, dict):
            body = _encode_params(data).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        elif isinstance(data, str):
            body, hdrs["Content-Type"] = data.encode(), "text/plain; charset=utf-8"
        elif isinstance(data, bytes):
            body = data
        auto = token is None and _urlparse.urlsplit(url).path.startswith("/admin/")
        if isinstance(token, Response):
            token = token["access_token"]
        if auto:
            token = self.admin_token()
        if token:
            hdrs["Authorization"] = f"Bearer {token}"
        hdrs.update(self.headers)
        hdrs.update(headers or {})
        opener = self._follow if allow_redirects else self._nofollow
        r = _send(opener, method, url, body, hdrs, timeout or self.timeout)
        if r.status_code == 401 and auto and not (headers and "Authorization" in headers):
            hdrs["Authorization"] = f"Bearer {self.admin_token(refresh=True)}"
            r = _send(opener, method, url, body, hdrs, timeout or self.timeout)
        return r

    def get(self, path: str, params: dict[str, Any] | None = None, **kw: Any) -> Response:
        """request("GET", ...)."""
        return self.request("GET", path, params, **kw)

    def post(self, path: str, data: Any = None, json: Any = None, **kw: Any) -> Response:
        """request("POST", ...)."""
        return self.request("POST", path, data=data, json=json, **kw)

    def put(self, path: str, data: Any = None, json: Any = None, **kw: Any) -> Response:
        """request("PUT", ...)."""
        return self.request("PUT", path, data=data, json=json, **kw)

    def patch(self, path: str, data: Any = None, json: Any = None, **kw: Any) -> Response:
        """request("PATCH", ...)."""
        return self.request("PATCH", path, data=data, json=json, **kw)

    def delete(self, path: str, **kw: Any) -> Response:
        """request("DELETE", ...)."""
        return self.request("DELETE", path, **kw)


def _b64url(s: str) -> bytes:
    return _base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Jwt(NamedTuple):
    """Decoded JWT; repr shows claims and iat/exp as local times."""

    header: dict[str, Any]
    claims: dict[str, Any]

    def __repr__(self) -> str:
        lines = [f"# header: {_json.dumps(self.header)}", _json.dumps(self.claims, indent=2)]
        now = _time.time()
        for k in ("iat", "nbf", "auth_time", "exp"):
            if isinstance(self.claims.get(k), int | float):
                t = self.claims[k]
                lines.append(f"# {k}={_time.ctime(t)} ({t - now:+.0f}s)")
        return "\n".join(lines)


def decode(token: str | Response, which: str = "access_token") -> Jwt:
    """Decode a JWT (no verification); a token Response decodes its `which` token."""
    if isinstance(token, Response):
        token = token[which]
    assert isinstance(token, str)
    h, c = token.split(".")[:2]
    return Jwt(_json.loads(_b64url(h)), _json.loads(_b64url(c)))


_DIGEST = {"HmacSHA1": _hashlib.sha1, "HmacSHA256": _hashlib.sha256, "HmacSHA512": _hashlib.sha512}


def _hotp(key: bytes, counter: int, digits: int, algorithm: str) -> str:
    """HOTP (RFC 4226); TOTP (RFC 6238) uses the time window as counter. Same as Keycloak
    HmacOTP / TimeBasedOTP."""
    h = _hmac.new(key, _struct.pack(">Q", counter), _DIGEST[algorithm]).digest()
    o = h[-1] & 0x0F
    code = (_struct.unpack(">I", h[o : o + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)


class _Otp:
    sub_type = ""

    def __init__(self, secret: str, digits: int = 6, algorithm: str = "HmacSHA1") -> None:
        """secret: the raw value Keycloak stores (secretData.value), not the base32 form."""
        self.secret, self.digits, self.algorithm = secret, digits, algorithm

    @classmethod
    def from_base32(cls, text: str, **kw: Any) -> Any:
        """From the base32 secret shown on the enrolment page."""
        s = text.replace(" ", "").upper()
        return cls(_base64.b32decode(s + "=" * (-len(s) % 8)).decode(), **kw)

    @property
    def base32(self) -> str:
        """The secret in base32."""
        return _base64.b32encode(self.secret.encode()).decode().rstrip("=")

    def _cred_data(self) -> dict[str, Any]:
        return {"subType": self.sub_type, "digits": self.digits, "algorithm": self.algorithm}

    def credential(self, label: str | None = None) -> dict[str, Any]:
        """Credential representation for creating a user with this OTP."""
        rep: dict[str, Any] = {
            "type": "otp",
            "secretData": _json.dumps({"value": self.secret}),
            "credentialData": _json.dumps(self._cred_data()),
        }
        if label is not None:
            rep["userLabel"] = label
        return rep


class TOTP(_Otp):
    """Time-based OTP; now() returns codes Keycloak accepts, never the same one twice.

    now() uses the current window, then the ones around it that Keycloak also accepts
    (+-look_ahead, realm otpPolicyLookAheadWindow, default 1), and only then waits.
    Keep one instance per credential for the whole session.
    """

    sub_type = "totp"

    def __init__(
        self,
        secret: str,
        digits: int = 6,
        period: int = 30,
        algorithm: str = "HmacSHA1",
        look_ahead: int = 1,
    ) -> None:
        super().__init__(secret, digits, algorithm)
        self.period, self.look_ahead = period, look_ahead
        self._used: set[int] = set()

    def code(self, at: float | None = None) -> str:
        """Code for time `at` (default now); does not mark it used."""
        t = _time.time() if at is None else at
        return _hotp(self.secret.encode(), int(t // self.period), self.digits, self.algorithm)

    def now(self) -> str:
        """An unused code that Keycloak accepts now (waits only if all are used)."""
        while True:
            current = int(_time.time() // self.period)
            ahead = range(current, current + self.look_ahead + 1)
            behind = range(current - 1, current - self.look_ahead - 1, -1)
            for w in (*ahead, *behind):
                if w not in self._used:
                    self._used = {u for u in self._used if u >= current - self.look_ahead}
                    self._used.add(w)
                    return self.code(w * self.period)
            _time.sleep(0.5)

    def _cred_data(self) -> dict[str, Any]:
        return {**super()._cred_data(), "period": self.period, "counter": 0}

    def __repr__(self) -> str:
        return f"TOTP({self.secret!r}, digits={self.digits}, period={self.period})"


class HOTP(_Otp):
    """Counter-based OTP; next() returns the code for counter and increments it."""

    sub_type = "hotp"

    def __init__(
        self, secret: str, counter: int = 0, digits: int = 6, algorithm: str = "HmacSHA1"
    ) -> None:
        super().__init__(secret, digits, algorithm)
        self.counter = counter

    def code(self, counter: int) -> str:
        """Code for this counter value."""
        return _hotp(self.secret.encode(), counter, self.digits, self.algorithm)

    def next(self) -> str:
        """Code for counter, then counter += 1."""
        c = self.code(self.counter)
        self.counter += 1
        return c

    def _cred_data(self) -> dict[str, Any]:
        return {**super()._cred_data(), "counter": self.counter, "period": 0}

    def __repr__(self) -> str:
        return f"HOTP({self.secret!r}, counter={self.counter}, digits={self.digits})"


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}
_SKIP_TEXT = {"script", "style", "head", "title"}
# JavaScript that submits a form: document.forms[0] / forms['id'] / getElementById('id'),
# followed by .requestSubmit() or .submit(). Group 1: index or id.
_JS_SUBMIT = _re.compile(
    r"""(?:forms\[\s*['"]?|getElementById\(\s*['"])([\w.:-]+)['"]?\s*[\])]"""
    r"""\s*\.(?:requestSubmit|submit)\(\)"""
)


class Button(NamedTuple):
    """Submit button of a Form; label is its visible text or value."""

    name: str
    value: str
    id: str
    label: str

    def __repr__(self) -> str:
        return repr(self.label or self.name or self.id)


class Form:
    """A <form>: fields (values a browser would send), names, buttons.

    fields: hidden and pre-filled inputs as a browser would submit them unchanged.
    names: every field name in the form. buttons: submit buttons in document order.
    """

    def __init__(self, attrs: dict[str, str | None]) -> None:
        self.id = attrs.get("id")
        self.action = attrs.get("action") or ""
        self.method = (attrs.get("method") or "get").upper()
        self.fields: dict[str, str] = {}
        self.names: list[str] = []
        self.buttons: list[Button] = []

    def button(self, which: str) -> Button | None:
        """Submit button by name, id or label."""
        for b in self.buttons:
            if which in (b.name, b.id) or which.lower() == b.label.lower():
                return b
        return None

    def __repr__(self) -> str:
        action = _urlparse.urlsplit(self.action).path
        return f"<Form {self.id} {self.names} buttons={self.buttons} -> {self.method} {action}>"


class _PageParser(_HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page_id: str | None = None
        self.title = ""
        self.forms: list[Form] = []
        self.links: list[tuple[str, str, str]] = []  # (id, text, href)
        self.clickables: list[tuple[str, str]] = []  # (text, form ref) of onclick submitters
        self.by_id: dict[str, list[str]] = {}
        self.messages: list[str] = []
        self.text: list[str] = []
        self._form: Form | None = None
        self._select: str | None = None
        # (tag, id, is_message, clickable index or -1)
        self._stack: list[tuple[str, str | None, bool, int]] = []
        self._click_text: list[list[str]] = []
        self._link: dict[str, Any] | None = None
        self._button: dict[str, Any] | None = None
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "body":
            self.page_id = a.get("data-page-id")  # Keycloak login theme
        if tag in _SKIP_TEXT:
            self._skip += 1
            self._in_title = tag == "title"
        cls, el_id = a.get("class") or "", a.get("id")
        # Keycloak login theme alerts and field errors, or any role="alert".
        is_msg = (
            "kc-feedback-text" in cls
            or (el_id or "").startswith("input-error")
            or a.get("role") == "alert"
        )
        click = -1
        m = _JS_SUBMIT.search(a.get("onclick") or "")
        if m and tag not in _VOID:
            click = len(self.clickables)
            self.clickables.append(("", m.group(1)))
            self._click_text.append([])
        if tag not in _VOID:
            self._stack.append((tag, el_id, is_msg, click))
            if el_id:
                self.by_id[el_id] = []
            if is_msg:
                self.messages.append("")
        if tag == "form":
            self._form = Form(a)
            self.forms.append(self._form)
        elif tag == "a" and a.get("href"):
            self._link = {"id": el_id or "", "href": a["href"], "text": []}
        elif self._form is not None:
            self._form_control(tag, a)

    def _form_control(self, tag: str, a: dict[str, str | None]) -> None:
        f, name = self._form, a.get("name") or ""
        assert f is not None
        if tag not in ("input", "button", "select", "textarea", "option"):
            return
        typ = (a.get("type") or ("submit" if tag == "button" else "text")).lower()
        if typ in ("submit", "image") and tag in ("input", "button"):
            b = {"name": name, "value": a.get("value") or "", "id": a.get("id") or ""}
            if tag == "button":
                self._button = {**b, "text": []}
            else:
                f.buttons.append(Button(**b, label=b["value"]))
            return
        if tag == "option":
            if self._select and (self._select not in f.fields or "selected" in a):
                f.fields[self._select] = a.get("value") or ""
            return
        if not name or typ in ("button", "reset", "file"):
            return
        if name not in f.names:
            f.names.append(name)
        if tag == "select":
            self._select = name
        elif typ in ("checkbox", "radio"):
            if "checked" in a:
                f.fields[name] = a.get("value") or "on"
        else:
            f.fields[name] = a.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TEXT:
            self._skip = max(0, self._skip - 1)
            self._in_title = False
        if tag == "form":
            self._form = None
        elif tag == "select":
            self._select = None
        elif tag == "button" and self._button is not None and self._form is not None:
            b = self._button
            label = _squash(b.pop("text")) or b["value"]
            self._form.buttons.append(Button(**b, label=label))
            self._button = None
        elif tag == "a" and self._link is not None:
            lk = self._link
            self.links.append((lk["id"], _squash(lk["text"]), lk["href"]))
            self._link = None
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                for *_, click in self._stack[i:]:
                    if click >= 0:
                        ref = self.clickables[click][1]
                        self.clickables[click] = (_squash(self._click_text[click]), ref)
                del self._stack[i:]
                break

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._skip:
            return
        self.text.append(data)
        for buf in (self._link, self._button):
            if buf is not None:
                buf["text"].append(data)
        msg_depth = 0
        for _tag, el_id, is_msg, click in self._stack:
            if el_id:
                self.by_id[el_id].append(data)
            if click >= 0:
                self._click_text[click].append(data)
            msg_depth += is_msg
        if msg_depth:
            self.messages[-1] += data


def _squash(parts: list[str]) -> str:
    return " ".join(" ".join(parts).split())


class Page:
    """HTML page: id, messages, forms, links; page["field"] reads a form field.

    id: the Keycloak login theme's <body data-page-id> (e.g. login-login-otp), else None.
    messages: Keycloak alerts and field errors, and elements with role="alert".
    page["name"]: value of a form field (from the only form that has it).
    Attributes: id, status_code, url, title, messages, forms, links (id, text, href),
    clickables (text, form) of onclick elements that submit a form, text, html, response.
    """

    def __init__(self, response: Response) -> None:
        self.response, self.status_code, self.url = response, response.status_code, response.url
        self.html = response.text
        p = _PageParser()
        p.feed(self.html)
        p.close()
        self.id, self.title, self.forms = p.page_id, " ".join(p.title.split()), p.forms
        self.links, self.clickables = p.links, p.clickables
        self.messages = [m for m in (" ".join(m.split()) for m in p.messages) if m]
        self.text = _squash(p.text)
        self._by_id = {k: _squash(v) for k, v in p.by_id.items()}

    def text_of(self, el_id: str) -> str | None:
        """Text of the element with this id, or None."""
        return self._by_id.get(el_id)

    def form(
        self,
        form_id: str | None = None,
        where: dict[str, str] | None = None,
        names: Sequence[str] = (),
        button: str | None = None,
    ) -> Form:
        """The only form matching id, field values, field names and button.

        Raises LookupError, listing the forms, unless exactly one form matches.
        """
        found = [
            f
            for f in self.forms
            if (form_id is None or f.id == form_id)
            and all(f.fields.get(k) == v for k, v in (where or {}).items())
            and all(n in f.names for n in names)
            and (button is None or f.button(button) is not None)
        ]
        if len(found) == 1:
            return found[0]
        crit = {"id": form_id, "where": where, "names": list(names) or None, "button": button}
        crit = {k: v for k, v in crit.items() if v}
        what = "no form matches" if not found else f"{len(found)} forms match"
        raise LookupError(f"{what} {crit}: {self.forms}")

    def __getitem__(self, name: str) -> str:
        return self.form(names=[name]).fields.get(name, "")

    def link(self, text_or_id: str) -> str:
        """href of the link with this id or text (else unique partial text)."""
        t = text_or_id.lower()
        for el_id, text, href in self.links:
            if text_or_id == el_id or t == text.lower():
                return href
        partial = [href for _, text, href in self.links if t in text.lower()]
        if len(partial) == 1:
            return partial[0]
        raise LookupError(f"no link {text_or_id!r}: {[(i, t) for i, t, _ in self.links]}")

    def __repr__(self) -> str:
        parts = [f"<Page {self.id or repr(self.title)} {self.status_code}"]
        parts.append(f"forms={[f.id for f in self.forms]}")
        if self.messages:
            parts.append(f"messages={self.messages}")
        return " ".join(parts) + ">"

    def __str__(self) -> str:
        return self.text


class Callback(NamedTuple):
    """The redirect to a stop_at URL that ended a flow: url, params."""

    url: str
    params: dict[str, str]

    @property
    def code(self) -> str | None:
        """The code parameter."""
        return self.params.get("code")

    @property
    def error(self) -> str | None:
        """The error parameter (error_description is in params)."""
        return self.params.get("error")

    def __repr__(self) -> str:
        shown = {k: (v[:12] + "..." if len(v) > 16 else v) for k, v in self.params.items()}
        return f"<Callback {self.url.split('?')[0]} {shown}>"


class AuthRequest(NamedTuple):
    """The request sent by authorize(); print() lists every parameter.

    Also keeps the PKCE verifier and the token endpoint used by tokens().
    """

    url: str
    params: dict[str, str]
    verifier: str | None
    token_url: str
    secret: str | None

    @property
    def client(self) -> str:
        """The client_id parameter."""
        return self.params["client_id"]

    @property
    def redirect_uri(self) -> str:
        """The redirect_uri parameter."""
        return self.params["redirect_uri"]

    def matches(self, callback: Callback | None) -> bool:
        """True if the callback carries this request's state."""
        return callback is not None and callback.params.get("state") == self.params.get("state")

    def __repr__(self) -> str:
        lines = [f"<AuthRequest GET {self.url.split('?')[0]}"]
        lines += [f"  {k}={v}" for k, v in self.params.items()]
        if self.verifier:
            lines.append(f"  (code_verifier={self.verifier})")
        return "\n".join(lines) + ">"


class SimpleBrowser:
    """Form browser over HTTP: cookies, redirects, forms, links; no JavaScript.

    SPAs (admin and account console), WebAuthn and script-driven pages do not work.
    Secure cookies are kept also over http.

    kc supplies the transport: base URL for relative paths, TLS, connect_to and headers.
    Without kc, open() needs absolute URLs. A redirect to a URL in stop_at is not followed:
    it ends the flow as b.callback (authorize() adds its redirect_uri).

    Switch node with b.kc = other (same cookies); for SSO to carry over, both Keycloak objects
    need the same url, with connect_to selecting the node (otherwise the issuer differs).

    Attributes: page (current Page), callback (Callback that ended the flow), auth (last
    AuthRequest), history (requests of the flow), jar (cookies), kc, stop_at.
    """

    max_redirects = 30

    def __init__(
        self, kc: Keycloak | None = None, stop_at: Sequence[str] = (), timeout: float = 30
    ) -> None:
        self.kc, self.stop_at, self.timeout = kc, list(stop_at), timeout
        self.jar = _CookieJar()
        self.page: Page | None = None
        self.callback: Callback | None = None
        self.history: list[str] = []
        self.auth: AuthRequest | None = None  # last authorize() request

    def __repr__(self) -> str:
        return f"<SimpleBrowser at {self.callback or self.page!r}>"

    @property
    def code(self) -> str | None:
        """Code of the last callback, or None."""
        return self.callback.code if self.callback else None

    def _stops(self, url: str) -> bool:
        a = _urlparse.urlsplit(url)
        for s in self.stop_at:
            b = _urlparse.urlsplit(s)
            if (a.scheme, a.netloc, a.path) == (b.scheme, b.netloc, b.path):
                return True
        return False

    def open(
        self, url: str, method: str = "GET", data: dict[str, str] | None = None
    ) -> Page | Callback:
        """Request url (or path relative to kc.url), following redirects."""
        kc = self.kc
        if kc is not None:
            url = kc.abs_url(url)
        elif "://" not in url:
            raise ValueError(f"relative URL {url!r} needs SimpleBrowser(kc)")
        handlers = kc.handlers() if kc else [_urlrequest.HTTPSHandler()]
        # Built per call: b.kc = other_kc switches node and keeps the cookies.
        opener = _urlrequest.build_opener(
            *handlers, _urlrequest.HTTPCookieProcessor(self.jar), _NoRedirect
        )
        body = _urlparse.urlencode(data).encode() if data is not None else None
        self.callback = None
        for _ in range(self.max_redirects):
            if self._stops(url):
                split = _urlparse.urlsplit(url)
                q = _urlparse.parse_qsl(split.query) + _urlparse.parse_qsl(split.fragment)
                self.callback = Callback(url, dict(q))
                self.history.append(f"{method} {split.path} (stop)")
                return self.callback
            hdrs = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
            hdrs.update(kc.headers if kc else {})
            if body is not None:
                hdrs["Content-Type"] = "application/x-www-form-urlencoded"
            r = _send(opener, method, url, body, hdrs, kc.timeout if kc else self.timeout)
            for c in self.jar:  # Keycloak marks cookies Secure; keep them over plain http
                c.secure = False
            self.history.append(f"{method} {_urlparse.urlsplit(url).path} {r.status_code}")
            if r.status_code in (301, 302, 303, 307, 308) and r.location:
                url = _urlparse.urljoin(url, r.location)
                if r.status_code in (301, 302, 303):
                    method, body = "GET", None
                continue
            self.page = Page(r)
            return self.page
        raise RuntimeError(f"more than {self.max_redirects} redirects: {self.history[-5:]}")

    def _page(self) -> Page:
        if self.page is None or self.callback is not None:
            raise RuntimeError(f"no current page: {self.callback!r}")
        return self.page

    def submit(
        self,
        form: str | None = None,
        *,
        where: dict[str, str] | None = None,
        button: str | None = None,
        **fields: str,
    ) -> Page | Callback:
        """Fill and post the only form that has all `fields` (and `button`).

        form (id) and where (field values) narrow the choice down. button: name, id or label
        of the submit button; default the first one, as when pressing Enter. Fields not passed
        keep their page values. Raises LookupError, listing the forms, unless exactly one matches.
        """
        page = self._page()
        f = page.form(form, where, names=list(fields), button=button)
        data = {**f.fields, **fields}
        pressed = f.button(button) if button else (f.buttons[0] if f.buttons else None)
        if pressed and pressed.name:
            data[pressed.name] = pressed.value
        action = _urlparse.urljoin(page.url, f.action)
        if f.method == "GET":
            return self.open(action.split("?")[0] + "?" + _urlparse.urlencode(data))
        return self.open(action, "POST", data)

    def _submit_ref(self, ref: str) -> Page | Callback:
        """Submit a form referenced from JavaScript by index or id, as is."""
        page = self._page()
        f = page.forms[int(ref)] if ref.isdigit() else page.form(ref)
        pressed = f.buttons[0] if f.buttons else None
        data = {**f.fields, **({pressed.name: pressed.value} if pressed and pressed.name else {})}
        action = _urlparse.urljoin(page.url, f.action)
        if f.method == "GET":
            return self.open(action.split("?")[0] + "?" + _urlparse.urlencode(data))
        return self.open(action, "POST", data)

    def click(self, target: str) -> Page | Callback:
        """Click a link, an onclick element that submits a form, or a button.

        Order: link (id or text, else unique partial text), element whose onclick submits a
        form (e.g. a selectable card; text equal to, else containing, target), submit button
        (name, id or label). JavaScript links and onclick handlers that call
        document.forms[...].submit() or requestSubmit() submit that form; other JavaScript
        raises LookupError.
        """
        page = self._page()
        try:
            href = page.link(target)
        except LookupError:
            href = None
        if href is not None:
            if not href.lower().startswith("javascript:"):
                return self.open(_urlparse.urljoin(page.url, href))
            m = _JS_SUBMIT.search(href)
            if not m:
                raise LookupError(f"link {target!r} runs JavaScript: {href}")
            return self._submit_ref(m.group(1))
        t = target.lower()
        cards = [r for text, r in page.clickables if text.lower() == t]
        cards = cards or [r for text, r in page.clickables if t in text.lower()]
        if len(cards) == 1:
            return self._submit_ref(cards[0])
        with_button = [f for f in page.forms if f.button(target)]
        if len(with_button) == 1 and not cards:
            return self.submit(with_button[0].id, button=target)
        links = [(i, t) for i, t, _ in page.links]
        cl = [text[:40] for text, _ in page.clickables]
        raise LookupError(
            f"nothing (or several) to click for {target!r}: links={links} clickables={cl} "
            f"forms={page.forms}"
        )

    def expect(self, *page_ids: str) -> Page:
        """Return the current page if its id is one of page_ids, else AssertionError."""
        if self.page is not None and self.callback is None and self.page.id in page_ids:
            return self.page
        state = self.callback or self.page
        raise AssertionError(f"expected page {' or '.join(page_ids)}, at {state!r}")

    def authorize(
        self,
        realm: str,
        client: str,
        redirect_uri: str = "http://localhost/cb",
        *,
        secret: str | None = None,
        scope: str = "openid",
        pkce: bool = True,
        **params: str,
    ) -> Page | Callback:
        """Start the OIDC code flow; params add or replace query parameters.

        E.g. kc_action, prompt, login_hint, kc_idp_hint, state, nonce. Without pkce, or with an
        own code_challenge, tokens() sends no generated verifier. The request is kept in b.auth.
        Returns the login page, or the callback right away if an SSO session exists.
        """
        q = {
            "client_id": client,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scope,
            "state": _secrets.token_urlsafe(16),
            "nonce": _secrets.token_urlsafe(16),
        }
        verifier = None
        if pkce and "code_challenge" not in params:
            verifier = _secrets.token_urlsafe(48)
            digest = _hashlib.sha256(verifier.encode()).digest()
            q["code_challenge"] = _base64.urlsafe_b64encode(digest).decode().rstrip("=")
            q["code_challenge_method"] = "S256"
        q.update(params)
        base = f"/realms/{realm}/protocol/openid-connect"
        url = f"{base}/auth?{_urlparse.urlencode(q)}"
        if self.kc is not None:
            url = self.kc.abs_url(url)
        self.auth = AuthRequest(url, q, verifier, f"{base}/token", secret)
        if redirect_uri not in self.stop_at:
            self.stop_at.append(redirect_uri)
        self.history = []
        return self.open(url)

    def tokens(self, **overrides: str) -> Response:
        """Exchange the callback's code; overrides replace form parameters.

        E.g. code_verifier="wrong" to test PKCE; "" removes a parameter.
        """
        if self.auth is None or self.kc is None or not self.code:
            raise RuntimeError(f"no code from authorize(): {self.callback or self.page!r}")
        a = self.auth
        data = {
            "grant_type": "authorization_code",
            "code": self.code,
            "redirect_uri": a.redirect_uri,
            "client_id": a.client,
        }
        if a.verifier:
            data["code_verifier"] = a.verifier
        if a.secret:
            data["client_secret"] = a.secret
        data.update(overrides)
        data = {k: v for k, v in data.items() if v != ""}
        return self.kc.post(a.token_url, data=data, token="")


_API = (Keycloak, Response, HTTPError, decode, Jwt, TOTP, HOTP)
_API_BROWSER = (SimpleBrowser, AuthRequest, Callback, Page, Form, Button)


def _summary(obj: Any) -> str:
    return ((_inspect.getdoc(obj) or "").splitlines() or [""])[0]


def _signature(obj: Any) -> str:
    try:
        sig = _inspect.signature(obj)
    except (TypeError, ValueError):
        return "(...)"
    params = [
        p.replace(annotation=_inspect.Parameter.empty)
        for p in sig.parameters.values()
        if p.name not in ("self", "cls")
    ]
    ret = sig.return_annotation
    text = str(sig.replace(parameters=params, return_annotation=_inspect.Signature.empty))
    return text + (f" -> {ret}" if ret is not _inspect.Signature.empty and ret != "None" else "")


def api(name: Any = None) -> None:
    """Print the driver API: signatures and one-line summaries.

    api(SimpleBrowser) prints one class; help(SimpleBrowser.submit) shows the details.
    """
    items = [name] if name is not None else [*_API, *_API_BROWSER]
    for obj in items:
        if not isinstance(obj, type):
            print(f"{obj.__name__}{_signature(obj)}  # {_summary(obj)}")
            continue
        print(f"{obj.__name__}{_signature(obj)}  # {_summary(obj)}")
        seen: set[str] = set()
        for cls in obj.__mro__:
            if cls in (object, tuple, Exception, BaseException):
                break
            for attr, member in vars(cls).items():
                if attr.startswith("_") or attr in seen:
                    continue
                seen.add(attr)
                if isinstance(member, property):
                    print(f"  .{attr}  # {_summary(member)}")
                elif isinstance(member, (classmethod, staticmethod)):
                    fn = member.__func__
                    print(f"  .{attr}{_signature(fn)}  # {_summary(fn)}")
                elif callable(member):
                    print(f"  .{attr}{_signature(member)}  # {_summary(member)}")
