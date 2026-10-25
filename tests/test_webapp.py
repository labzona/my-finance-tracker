from datetime import date
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from werkzeug.security import generate_password_hash
from webapp import create_app
from tracker_store import Store

ORIGIN = "https://localhost"


def field(html, name):
    return re.search(r'name="' + name + r'" value="([^"]+)"', html).group(1)


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.passwords = {"ola": secrets.token_urlsafe(20), "marko": secrets.token_urlsafe(20)}
        cls.hashes = {owner: generate_password_hash(value) for owner, value in cls.passwords.items()}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = {"APP_BASE_URL": ORIGIN, "APP_SECRET_KEY": secrets.token_urlsafe(48),
                       "STORAGE_DIR": self.temp.name, "TESTING": "1"}
        for owner in self.passwords:
            self.config[f"{owner.upper()}_USERNAME"] = owner
            self.config[f"{owner.upper()}_PASSWORD_HASH"] = self.hashes[owner]
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.store = self.app.extensions["tracker_store"]

    def get(self, path):
        return self.client.get(path, base_url=ORIGIN)

    def login(self, owner="marko"):
        page = self.get("/login")
        return self.client.post("/login", base_url=ORIGIN, data={
            "csrf": field(page.text, "csrf"), "username": owner, "password": self.passwords[owner]})

    def cash_form(self, **changes):
        page = self.get("/fava/marko/extension/Household/?month=2026-10")
        self.assertEqual(page.status_code, 200)
        values = {"csrf": field(page.text, "csrf"), "nonce": field(page.text, "nonce"),
                  "date": "2026-10-16", "amount": "1,00", "category": "Expenses:Кафе",
                  "description": "Тестова готівка 1 PLN"}
        return {**values, **changes}

    def post_cash(self, values):
        return self.client.post("/cash", base_url=ORIGIN, data=values)

    def test_all_financial_routes_require_login(self):
        for path in ["/", "/fava/marko/extension/Household/", "/fava/ola/api/ledger_data",
                     "/fava/marko/api/source?path=/etc/passwd", "/data/statement.csv",
                     "/runtime/tracker.sqlite3", "/report.html", "/main.beancount", "/.env"]:
            response = self.get(path)
            self.assertEqual(response.status_code, 303, path)
            self.assertNotIn(b"2195", response.data)

    def test_https_only_no_forwarded_header_bypass(self):
        response = self.client.get("/", base_url="http://localhost", headers={"X-Forwarded-Proto": "https"})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.location, "https://localhost/login")
        self.assertEqual(self.client.get("/login", base_url="https://evil.invalid").status_code, 400)

    def test_login_cookies_headers_and_controls(self):
        response = self.login()
        self.assertEqual(response.status_code, 303)
        cookie = response.headers["Set-Cookie"]
        for flag in ["Secure", "HttpOnly", "SameSite=Lax"]:
            self.assertIn(flag, cookie)
        page = self.get("/fava/marko/extension/Household/?month=2026-08")
        self.assertEqual(page.status_code, 200)
        self.assertIn("1 855,57", page.text)
        self.assertIn("Заощадження", page.text)
        self.assertEqual(page.headers["Cache-Control"], "private, no-store")
        self.assertIn("Strict-Transport-Security", page.headers)
        self.assertIn("/fava/static/", page.text)
        self.assertIn('name="date"', page.text)

    def test_owner_isolation_and_fava_write_block(self):
        self.login()
        self.assertEqual(self.get("/fava/ola/extension/Household/").status_code, 404)
        self.assertEqual(self.get("/fava/ola/api/journal").status_code, 404)
        page = self.get("/fava/marko/journal/")
        self.assertNotIn("Product Co", page.text)
        api = self.get("/fava/marko/api/ledger_data")
        self.assertEqual(api.status_code, 200)
        self.assertNotIn("Product Co", api.text)
        self.assertNotIn("Rent — Mokotów", api.text)
        for path in ["/fava/marko/api/query?query_string=SELECT%20*", "/fava/marko/api/source",
                     "/fava/marko/api/extract", "/fava/marko/download-journal/", "/fava/marko/editor/"]:
            self.assertEqual(self.get(path).status_code, 403, path)
        form = self.cash_form()
        response = self.client.put("/fava/marko/api/source", base_url=ORIGIN, data={"csrf": form["csrf"]})
        self.assertEqual(response.status_code, 403)

    def test_cash_one_pln_duplicate_reload_restart_and_bean_check(self):
        self.login()
        before = self.store.monthly("marko", "2026-10")["selected"]["variable_pln"]
        values = self.cash_form()
        self.assertEqual(self.post_cash(values).status_code, 303)
        self.assertEqual(self.post_cash(values).status_code, 303)
        page = self.get("/fava/marko/extension/Household/?month=2026-10")
        self.assertIn("Тестова готівка 1 PLN", page.text)
        self.assertIn("1,00 PLN", page.text)
        self.assertEqual(len(self.store.cash_rows("marko")), 1)
        self.assertEqual(self.store.monthly("marko", "2026-10")["selected"]["variable_pln"], before+1)
        # New app regenerates bank facts, keeps DB, and reuses the existing session.
        cookie = self.client.get_cookie("tracker_session")
        restarted = create_app(self.config)
        client = restarted.test_client()
        client.set_cookie("tracker_session", cookie.value)
        after = client.get("/fava/marko/extension/Household/?month=2026-10", base_url=ORIGIN)
        self.assertEqual(after.status_code, 200)
        self.assertIn("Тестова готівка 1 PLN", after.text)
        for owner in ("ola", "marko"):
            result = subprocess.run(["uv", "run", "--offline", "--no-sync", "bean-check", str(self.store.book(owner))], capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        for owner, amounts in [("ola", ("2195.21", "2582.42")), ("marko", ("1855.57", "2432.83"))]:
            for month, expected in zip(("2026-08", "2026-09"), amounts):
                self.assertEqual(str(self.store.monthly(owner, month)["bank_variable"]), expected)

    def test_csrf_tampered_nonce_and_forged_owner_rejected(self):
        self.login()
        values = self.cash_form()
        self.assertEqual(self.post_cash({**values, "csrf": "bad"}).status_code, 403)
        self.assertEqual(self.post_cash({**values, "csrf": "ї"}).status_code, 403)
        self.assertEqual(self.post_cash({**values, "nonce": "bad"}).status_code, 403)
        self.assertEqual(self.post_cash({**values, "owner": "ola"}).status_code, 400)
        self.assertEqual(len(self.store.cash_rows("marko")), 0)

    def test_invalid_cash_and_unknown_month_rejected(self):
        self.login()
        values = self.cash_form()
        for changes in [{"amount": "0"}, {"amount": "-1"}, {"amount": "1.001"},
                        {"amount": "NaN"}, {"category": "Income:Зарплата"},
                        {"description": "a\n2026-08-01"}, {"date": "2099-01-01"}]:
            self.assertEqual(self.post_cash({**values, **changes}).status_code, 400, changes)
        self.assertEqual(self.get("/fava/marko/extension/Household/?month=invalid").status_code, 400)
        self.assertEqual(len(self.store.cash_rows("marko")), 0)

    def test_submission_token_cannot_cross_user_or_session(self):
        self.login()
        old = self.cash_form()
        self.client.post("/logout", base_url=ORIGIN, data={"csrf": old["csrf"]})
        self.login("ola")
        page = self.get("/fava/ola/extension/Household/?month=2026-10")
        self.assertEqual(self.post_cash({**old, "csrf": field(page.text, "csrf")}).status_code, 403)
        self.client.post("/logout", base_url=ORIGIN, data={"csrf": field(page.text, "csrf")})
        self.login()
        fresh = self.cash_form()
        self.assertEqual(self.post_cash({**old, "csrf": fresh["csrf"]}).status_code, 403)
        self.assertEqual(len(self.store.cash_rows("ola")) + len(self.store.cash_rows("marko")), 0)

    def test_logout_revokes_copied_cookie(self):
        self.login()
        values = self.cash_form()
        old = self.client.get_cookie("tracker_session").value
        self.assertEqual(self.client.post("/logout", base_url=ORIGIN, data={"csrf": values["csrf"]}).status_code, 303)
        self.client.set_cookie("tracker_session", old)
        self.assertEqual(self.get("/").status_code, 303)
        self.assertEqual(self.get("/").location, "/login")

    def test_expired_session_and_password_rotation(self):
        self.login()
        with self.store.db() as db:
            db.execute("UPDATE sessions SET expires=0")
        self.assertEqual(self.get("/").location, "/login")
        self.login()
        cookie = self.client.get_cookie("tracker_session").value
        updated = dict(self.config)
        updated["MARKO_PASSWORD_HASH"] = self.hashes["ola"]
        client = create_app(updated).test_client()
        client.set_cookie("tracker_session", cookie)
        self.assertEqual(client.get("/", base_url=ORIGIN).location, "/login")

    def test_wrong_password_and_rate_limit_persist(self):
        page = self.get("/login")
        data = {"csrf": field(page.text, "csrf"), "username": "marko", "password": "wrong"}
        for _ in range(10):
            self.assertEqual(self.client.post("/login", base_url=ORIGIN, data=data).status_code, 401)
        client = create_app(self.config).test_client()
        page = client.get("/login", base_url=ORIGIN)
        data["csrf"] = field(page.text, "csrf")
        self.assertEqual(client.post("/login", base_url=ORIGIN, data=data).status_code, 429)

    def test_description_is_escaped_and_replay_cannot_change_amount(self):
        self.login()
        values = self.cash_form(description='<script>alert("x")</script>')
        self.assertEqual(self.post_cash(values).status_code, 303)
        self.assertEqual(self.post_cash({**values, "amount": "2"}).status_code, 400)
        page = self.get("/fava/marko/extension/Household/?month=2026-10")
        self.assertNotIn('<script>alert("x")</script>', page.text)
        self.assertIn("&lt;script&gt;", page.text)

    def test_cash_recovers_after_projection_write_failure(self):
        with patch.object(self.store, "refresh", side_effect=OSError("test disk error")):
            with self.assertRaises(OSError):
                self.store.add_cash("marko", "retry-key", "2026-10-16", "1", "Expenses:Кафе", "Recovery", date(2026,10,25))
        recovered = Store(Path(self.temp.name))
        self.assertEqual(len(recovered.cash_rows("marko")), 1)
        self.assertFalse(recovered.add_cash("marko", "retry-key", "2026-10-16", "1", "Expenses:Кафе", "Recovery", date(2026,10,25)))
        self.assertEqual(recovered.monthly("marko", "2026-10")["cash_total"], 1)

    def test_missing_configuration_fails_closed(self):
        with self.assertRaises(ValueError):
            create_app({})
        bad = dict(self.config)
        bad["MARKO_PASSWORD_HASH"] = "plain-password"
        with self.assertRaises(ValueError):
            create_app(bad)
        with self.assertRaises(ValueError):
            create_app({**self.config, "LOCAL_DEVELOPMENT": "1", "APP_BASE_URL": "https://public.invalid"})

    def test_backup_restores_cash_without_reviving_sessions(self):
        from manage import backup_database
        self.login()
        self.assertEqual(self.post_cash(self.cash_form()).status_code, 303)
        destination = Path(self.temp.name) / "backup.sqlite3"
        backup_database(self.store.database, destination)
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(destination)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM cash").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM sessions").fetchone()[0], 0)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            backup_database(self.store.database, destination)

    def test_secret_file_populates_environment_and_env_override_wins(self):
        import os
        import json
        from webapp import environment
        path = Path(self.temp.name) / "env.json"
        path.write_text(json.dumps({"APP_SECRET_KEY": secrets.token_urlsafe(40), "OLA_USERNAME": "file-login"}))
        path.chmod(0o600)
        with patch.dict(os.environ, {"TRACKER_ENV_FILE": str(path), "OLA_USERNAME": "env-login"}, clear=True):
            values = environment()
            self.assertEqual(values["OLA_USERNAME"], "env-login")
            self.assertEqual(os.environ["APP_SECRET_KEY"], values["APP_SECRET_KEY"])
        path.chmod(0o644)
        with patch.dict(os.environ, {"TRACKER_ENV_FILE": str(path)}, clear=True):
            with self.assertRaises(ValueError):
                environment()


if __name__ == "__main__":
    unittest.main()
