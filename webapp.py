"""Two-user authentication around existing Fava; no external auth provider."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import secrets
import re
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import Flask, Response, abort, g, redirect, render_template, request, session
from fava.application import create_app as create_fava
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash

from tracker_store import ROOT, OWNERS, InputError, Store


def environment():
    values = dict(os.environ)
    path = Path(values.get("TRACKER_ENV_FILE", ROOT / "runtime/env.json"))
    if path.exists():
        # File is a protected backing store for WSGI environment variables.
        if path.stat().st_mode & 0o077:
            raise ValueError("Файл конфігурації має бути доступний лише власнику (chmod 600)")
        for key, value in json.loads(path.read_text()).items():
            os.environ.setdefault(key, value)
    return dict(os.environ)


def create_app(config=None):
    config = dict(environment() if config is None else config)
    secret = config.get("APP_SECRET_KEY", "")
    if len(secret) < 32:
        raise ValueError("Потрібен випадковий APP_SECRET_KEY довжиною щонайменше 32 символи")
    origin = config.get("APP_BASE_URL", "")
    parsed = urlsplit(origin)
    local = config.get("LOCAL_DEVELOPMENT") == "1"
    if local and parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("Локальний HTTP дозволено лише на loopback")
    if (parsed.scheme != "https" and not (local and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"})) or not parsed.hostname or parsed.path not in {"", "/"} or parsed.username or parsed.query or parsed.fragment:
        raise ValueError("APP_BASE_URL має бути коректною HTTPS-адресою")
    users = {}
    credentials = {}
    for owner in OWNERS:
        username = config.get(f"{owner.upper()}_USERNAME", "")
        password_hash = config.get(f"{owner.upper()}_PASSWORD_HASH", "")
        valid_hash = re.fullmatch(r"(?:scrypt:32768:8:1\$[A-Za-z0-9]{16}\$[0-9a-f]{128}|pbkdf2:sha256:600000\$[A-Za-z0-9]{16}\$[0-9a-f]{64})", password_hash)
        if not username or len(username) > 80 or not valid_hash:
            raise ValueError("Потрібні два окремі логіни та надійні хеші паролів у середовищі")
        if username in users:
            raise ValueError("Логіни мають відрізнятися")
        users[username] = (owner, password_hash)
        credentials[owner] = Store.digest(password_hash)
    store = Store(Path(config.get("STORAGE_DIR", ROOT / "runtime")))
    app = Flask(__name__, template_folder="templates", static_folder="webstatic")
    app.config.update(SECRET_KEY=secret, SESSION_COOKIE_NAME="tracker_session",
                      SESSION_COOKIE_SECURE=parsed.scheme == "https", SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
                      MAX_CONTENT_LENGTH=8192, TRUSTED_HOSTS=[parsed.hostname],
                      TESTING=config.get("TESTING") == "1")
    if config.get("TRUST_PROXY_HEADERS") == "1":
        # Enable only behind PythonAnywhere's proxy, never on an open socket.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)
    app.extensions["tracker_store"] = store
    serializer = URLSafeTimedSerializer(secret, salt="cash-submit")
    favas = {}
    versions = {}
    for owner in OWNERS:
        child = create_fava([store.book(owner)], read_only=True, load=True, poll_watcher=True)
        child.config.update(TRACKER_OWNER=owner, TRACKER_STORE=store)
        child.register_error_handler(InputError, lambda error: ("Перевір параметри запиту.", 400))
        favas[owner] = child
        versions[owner] = store.book(owner).stat().st_mtime_ns

    def csrf_ok():
        supplied = request.form.get("csrf", "")
        return bool(supplied and secrets.compare_digest(supplied.encode(), session.get("csrf", "").encode()))

    @app.before_request
    def guard():
        if request.host.split(":")[0] != parsed.hostname:
            abort(400)
        if not request.is_secure and not (local and request.host.split(":")[0] in {"localhost", "127.0.0.1"}):
            # Never process passwords or writes over HTTP.
            return redirect(origin.rstrip("/") + "/login", code=303)
        session.setdefault("csrf", secrets.token_urlsafe(32))
        token = session.get("sid", "")
        owner = store.session_owner(token, credentials, time.time()) if token else None
        g.owner = owner
        if request.endpoint not in {"login", "static", "health"} and not owner:
            session.pop("sid", None)
            return redirect("/login", code=303)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not csrf_ok():
            abort(403)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'"
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.owner:
            return redirect("/")
        error = None
        status = 200
        if request.method == "POST":
            username = request.form.get("username", "")[:81]
            password = request.form.get("password", "")
            ip = request.remote_addr or "unknown"
            keys = [Store.digest("ip:"+ip), Store.digest("user:"+username)]
            if not store.login_attempt(keys, time.time()):
                error, status = "Забагато спроб. Спробуй через 15 хвилин.", 429
            else:
                found = users.get(username)
                dummy_hash = next(iter(users.values()))[1]
                valid = check_password_hash(found[1] if found else dummy_hash, password)
                if valid and found:
                    session.clear()
                    session["sid"] = store.new_session(found[0], credentials[found[0]], time.time())
                    session["csrf"] = secrets.token_urlsafe(32)
                    session.permanent = True
                    return redirect("/", code=303)
                error, status = "Неправильний логін або пароль.", 401
        return render_template("login.html", csrf=session["csrf"], error=error), status

    @app.post("/logout")
    def logout():
        store.logout(session.get("sid", ""))
        session.clear()
        return redirect("/login", code=303)

    @app.get("/")
    def home():
        return redirect(f"/fava/{g.owner}/extension/Household/")

    @app.post("/cash")
    def cash():
        allowed = {"csrf", "nonce", "date", "amount", "category", "description"}
        if set(request.form) != allowed or any(len(request.form.getlist(k)) != 1 for k in allowed):
            abort(400)
        try:
            payload = serializer.loads(request.form["nonce"], max_age=3600)
            if payload["owner"] != g.owner or payload["sid"] != Store.digest(session["sid"]):
                abort(403)
            when = request.form["date"]
            store.add_cash(g.owner, payload["id"], when, request.form["amount"],
                           request.form["category"], request.form["description"],
                           datetime.now(ZoneInfo("Europe/Warsaw")).date())
        except (BadSignature, SignatureExpired, KeyError):
            abort(403)
        except InputError as error:
            return render_template("message.html", message=str(error)), 400
        return redirect(f"/fava/{g.owner}/extension/Household/?month={when[:7]}", code=303)

    @app.route("/fava/", defaults={"path": ""})
    @app.route("/fava/<path:path>", methods=["GET", "HEAD", "POST", "PUT", "DELETE"])
    def fava_view(path):
        if request.method not in {"GET", "HEAD"}:
            abort(403)
        if not path.startswith(g.owner + "/") and not path.startswith("static/"):
            abort(404)
        if not path.startswith("static/"):
            parts = path.split("/")
            safe_apis = {"changed", "errors", "ledger_data", "options", "journal", "journal_page",
                         "income_statement", "balance_sheet", "trial_balance", "account_report",
                         "statistics", "commodities", "payee_accounts"}
            if len(parts) > 2 and parts[1] == "api" and parts[2] not in safe_apis:
                abort(403)
            if len(parts) > 1 and parts[1] not in {"", "api", "extension", "income_statement",
                    "balance_sheet", "trial_balance", "journal", "account", "holdings", "statistics"}:
                abort(403)
        with store.lock():
            book = store.refresh(g.owner)
            child = favas[g.owner]
            if versions[g.owner] != book.stat().st_mtime_ns:
                child.config["LEDGERS"][g.owner].load_file()
                versions[g.owner] = book.stat().st_mtime_ns
            environ = dict(request.environ)
            environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + "/fava"
            environ["PATH_INFO"] = "/" + path
            environ["tracker.csrf"] = session["csrf"]
            environ["tracker.nonce"] = serializer.dumps({"owner": g.owner,
                "sid": Store.digest(session["sid"]), "id": secrets.token_urlsafe(24)})
            return Response.from_app(child, environ)

    for code in (400, 403, 404, 413, 429, 500):
        app.register_error_handler(code, lambda error: ("Запит не виконано.", error.code))
    app.register_error_handler(InputError, lambda error: ("Перевір параметри запиту.", 400))
    return app
