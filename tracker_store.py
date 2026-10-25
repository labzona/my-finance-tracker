"""Persistent cash/session storage and private Fava projections of existing books."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import fcntl
import hashlib
import importlib
from io import StringIO
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
from threading import RLock

from beancount import loader
from beancount.core import data
from beancount.parser import printer

import report

ROOT = Path(__file__).resolve().parent
BANK = importlib.import_module("import")
OWNERS = {"ola": "Оля", "marko": "Марко"}


class InputError(ValueError):
    pass


class Store:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        if self.path.is_relative_to((ROOT / "data").resolve()):
            raise ValueError("Сховище не може бути в data/")
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path, 0o700)
        self.database = self.path / "tracker.sqlite3"
        self._lock = RLock()
        self._lock_depth = 0
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cash (
                  id INTEGER PRIMARY KEY, owner TEXT NOT NULL CHECK(owner IN ('ola','marko')),
                  request_id TEXT NOT NULL, date TEXT NOT NULL, cents INTEGER NOT NULL CHECK(cents>0),
                  category TEXT NOT NULL, description TEXT NOT NULL,
                  UNIQUE(owner, request_id));
                CREATE TABLE IF NOT EXISTS sessions (
                  token TEXT PRIMARY KEY, owner TEXT NOT NULL, credential TEXT NOT NULL,
                  expires REAL NOT NULL, idle REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (
                  key TEXT PRIMARY KEY, count INTEGER NOT NULL, until REAL NOT NULL);
            """)
        os.chmod(self.database, 0o600)
        self.categories = sorted({account for _, account, _ in BANK.load_rules()
                                  if account.startswith("Expenses:")})
        # Use unchanged educational inputs and existing import/accounting code.
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            (temp / "ola.csv").write_bytes((ROOT / "examples/ola-statement.csv").read_bytes())
            rows = BANK.ola_rows(temp) + BANK.marko_rows(ROOT / "examples/marko-statement.csv")
            BANK.write_ledger(rows, BANK.load_rules(), temp / "transactions.beancount")
            (temp / "main.beancount").write_text((ROOT / "main.beancount").read_text())
            self.entries, errors, _ = loader.load_file(str(temp / "main.beancount"))
            if errors:
                raise RuntimeError("Вихідний облік не пройшов перевірку")
        self.coverage = {owner: max(r.completed.date() for r in rows if r.owner == owner)
                         for owner in OWNERS}
        with self.lock():
            for owner in OWNERS:
                self.refresh(owner)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def lock(self):
        # One free web worker; also coordinate reload/CLI processes on Unix.
        with self._lock:
            if self._lock_depth:
                yield
                return
            with (self.path / "write.lock").open("a+") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                self._lock_depth = 1
                try:
                    yield
                finally:
                    self._lock_depth = 0
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def book(self, owner: str) -> Path:
        if owner not in OWNERS:
            raise InputError("Невідомий користувач")
        return self.path / f"{owner}.beancount"

    def cash_rows(self, owner: str, db=None):
        if db is not None:
            return db.execute("SELECT * FROM cash WHERE owner=? ORDER BY date,id", (owner,)).fetchall()
        with self.db() as connection:
            return self.cash_rows(owner, connection)

    def _render(self, owner: str, cash) -> str:
        bank_account = BANK.BANKS[owner]
        entries = []
        for entry in self.entries:
            if isinstance(entry, data.Transaction):
                if owner in entry.tags or (not entry.tags and any(p.account == bank_account for p in entry.postings)):
                    entries.append(entry)
            elif isinstance(entry, (data.Open, data.Balance)):
                if not entry.account.startswith("Assets:") or entry.account == bank_account:
                    entries.append(entry)
            elif isinstance(entry, data.Query):
                entries.append(entry._replace(query_string=entry.query_string.replace("'ola' IN tags", f"'{owner}' IN tags")))
        out = StringIO()
        out.write(f'option "title" "{owner}"\noption "operating_currency" "PLN"\n')
        printer.print_entries(entries, file=out)
        out.write('\n2026-07-31 open Assets:Готівка PLN\n')
        for row in cash:
            # No bank withdrawal or opening cash balance is invented.
            narration = BANK.escape_beancount(row["description"])
            amount = Decimal(row["cents"]) / 100
            out.write(f'\n{row["date"]} * "{narration}" #{owner} #cash\n'
                      f'  cash_id: "{row["id"]}"\n'
                      f'  Assets:Готівка  {-amount:.2f} PLN\n'
                      f'  {row["category"]}  {amount:.2f} PLN\n')
        out.write('\n2026-07-31 custom "fava-extension" "tracker_ext"\n')
        out.write('2026-07-31 custom "fava-option" "default-page" "extension/Household/"\n')
        out.write('2026-07-31 custom "fava-option" "language" "uk"\n')
        out.write('2026-07-31 custom "fava-option" "sidebar-show-queries" "0"\n')
        text = out.getvalue()
        _, errors, _ = loader.load_string(text)
        if errors:
            raise InputError("Операція не пройшла перевірку Beancount")
        return text

    def refresh(self, owner: str) -> Path:
        path = self.book(owner)
        text = self._render(owner, self.cash_rows(owner))
        if not path.exists() or path.read_text() != text:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path, delete=False) as fh:
                    temporary = Path(fh.name)
                    fh.write(text); fh.flush(); os.fsync(fh.fileno())
                temporary.replace(path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return path

    def add_cash(self, owner, request_id, when, amount, category, description, today):
        import re
        try:
            parsed = date.fromisoformat(when)
        except (ValueError, TypeError):
            raise InputError("Перевір дату") from None
        if parsed.isoformat() != when or not date(2026, 8, 1) <= parsed <= today:
            raise InputError("Дата має бути від 01.08.2026 до сьогодні")
        if not re.fullmatch(r"\d{1,7}(?:[.,]\d{1,2})?", amount.strip()):
            raise InputError("Сума має містити не більше двох знаків після коми")
        number = BANK.money(amount.strip())
        if not Decimal("0.01") <= number <= Decimal("1000000"):
            raise InputError("Сума має бути від 0,01 до 1 000 000 PLN")
        if category not in self.categories:
            raise InputError("Обери категорію зі списку")
        description = description.strip()
        if not description or len(description) > 200 or any(ord(c) < 32 for c in description):
            raise InputError("Опис: 1–200 символів, без перенесення рядка")
        values = (owner, request_id, parsed.isoformat(), int(number * 100), category, description)
        with self.lock():
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute("SELECT * FROM cash WHERE owner=? AND request_id=?", values[:2]).fetchone()
                if existing:
                    if tuple(existing[k] for k in ("date", "cents", "category", "description")) != values[2:]:
                        raise InputError("Повторний запит містить інші дані; онови сторінку")
                    created = False
                else:
                    db.execute("INSERT INTO cash(owner,request_id,date,cents,category,description) VALUES(?,?,?,?,?,?)", values)
                    # Validate candidate including the uncommitted row first.
                    self._render(owner, self.cash_rows(owner, db))
                    created = True
            # DB is authoritative. A crash here is repaired by refresh on read.
            self.refresh(owner)
            return created

    def monthly(self, owner, month=None):
        with self.lock():
            path = self.refresh(owner)
            months = report.load_report(owner, path)
            if month is None:
                selected = months[-1]
            else:
                selected = next((m for m in months if f'{m["year"]}-{m["month"]:02d}' == month), None)
                if selected is None:
                    raise InputError("Невідомий місяць")
            key = f'{selected["year"]}-{selected["month"]:02d}'
            cash = [dict(r) for r in self.cash_rows(owner) if r["date"].startswith(key)]
            cash_total = sum((Decimal(r["cents"]) / 100 for r in cash), Decimal(0))
            import calendar
            end = date(selected["year"], selected["month"], calendar.monthrange(selected["year"], selected["month"])[1])
            return {"selected": selected, "months": months, "key": key, "cash": cash,
                    "cash_total": cash_total, "bank_variable": selected["variable_pln"] - cash_total,
                    "coverage": self.coverage[owner], "partial": end > self.coverage[owner]}

    def login_attempt(self, keys, now):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM attempts WHERE until<=?", (now,))
            for key in keys:
                row = db.execute("SELECT count FROM attempts WHERE key=?", (key,)).fetchone()
                if row and row["count"] >= 10:
                    return False
            for key in keys:
                db.execute("INSERT INTO attempts(key,count,until) VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1", (key, now+900))
            return True

    def new_session(self, owner, credential, now):
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute("DELETE FROM sessions WHERE expires<=? OR idle<=?", (now, now))
            db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (self.digest(token), owner, credential, now+28800, now+1800))
        return token

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def session_owner(self, token, credentials, now):
        with self.db() as db:
            row = db.execute("SELECT * FROM sessions WHERE token=?", (self.digest(token),)).fetchone()
            if not row or row["expires"] <= now or row["idle"] <= now:
                return None
            owner = row["owner"]
            if credentials.get(owner) != row["credential"]:
                return None
            if row["idle"] < now+1740:
                db.execute("UPDATE sessions SET idle=? WHERE token=?", (now+1800, row["token"]))
            return owner

    def logout(self, token):
        with self.db() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (self.digest(token),))
