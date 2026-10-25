"""Local setup / backup / deployment bundle. Never publishes anything."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import secrets
import sqlite3
import zipfile
from contextlib import closing

from werkzeug.security import generate_password_hash
from tracker_store import ROOT


def backup_database(source, destination):
    source, destination = Path(source), Path(destination)
    if not source.is_file() or destination.exists() or destination.resolve().is_relative_to((ROOT / "data").resolve()):
        raise ValueError("Потрібна наявна база й новий файл копії поза data/")
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)
        # A restored backup must never revive previously revoked sessions.
        dst.execute("DELETE FROM sessions")
        dst.execute("DELETE FROM attempts")
        dst.commit()


def configure(origin, local=False):
    target = ROOT / "runtime/env.json"
    if target.exists():
        raise SystemExit("Конфігурація вже існує. Збережи її копію та явно перейменуй перед заміною.")
    passwords = []
    for owner in ("ola", "marko"):
        first = getpass.getpass(f"Новий окремий пароль для {owner} (12+ символів): ")
        second = getpass.getpass("Повтори пароль: ")
        if first != second or len(first) < 12 or first in passwords:
            raise SystemExit("Потрібні різні паролі від 12 символів та однакове підтвердження.")
        passwords.append(first)
    values = {"APP_BASE_URL": origin.rstrip("/"), "APP_SECRET_KEY": secrets.token_urlsafe(48),
              "STORAGE_DIR": str(ROOT / "runtime"), "OLA_USERNAME": "ola", "MARKO_USERNAME": "marko",
              "OLA_PASSWORD_HASH": generate_password_hash(passwords[0]),
              "MARKO_PASSWORD_HASH": generate_password_hash(passwords[1])}
    if local:
        values["LOCAL_DEVELOPMENT"] = "1"
    from webapp import create_app
    create_app(values)  # Validate before saving secrets.
    target.parent.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(values, fh, indent=2)
        fh.write("\n")
    print(f"Збережено захищену конфігурацію: {target}. Паролі не виводилися й не зберігалися відкритим текстом.")


def bundle():
    output = ROOT / "outputs/tracker-deploy.zip"
    output.parent.mkdir(exist_ok=True)
    files = ["pyproject.toml", "uv.lock", ".python-version", ".gitignore", ".gitattributes", "main.beancount", "rules.csv",
             "import.py", "report.py", "verify.py", "tracker_store.py", "tracker_ext.py",
             "webapp.py", "wsgi.py", "manage.py", "README.md", "deploy/PYTHONANYWHERE.md",
             "homework/18-implementation-audit.md", "homework/19-web-implementation.md"]
    for directory in ("examples", "templates", "webstatic", "tests"):
        files += [str(p.relative_to(ROOT)) for p in (ROOT / directory).rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(ROOT / name, name)
    print(f"Підготовлено {output}; конфігурації, сесій, готівки й приватних даних в архіві немає.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("configure")
    setup.add_argument("--origin", required=True)
    setup.add_argument("--local-http", action="store_true")
    sub.add_parser("serve")
    backup = sub.add_parser("backup")
    backup.add_argument("destination", type=Path)
    sub.add_parser("bundle")
    args = parser.parse_args()
    if args.command == "configure":
        configure(args.origin, args.local_http)
    elif args.command == "serve":
        from webapp import create_app, environment
        values = environment()
        if values.get("LOCAL_DEVELOPMENT") != "1" or values.get("APP_BASE_URL") != "http://127.0.0.1:5050":
            raise SystemExit("serve призначений лише для локальної перевірки на http://127.0.0.1:5050")
        create_app(values).run(host="127.0.0.1", port=5050, debug=False, use_reloader=False)
    elif args.command == "backup":
        from webapp import environment
        values = environment()
        source = Path(values.get("STORAGE_DIR", ROOT / "runtime")) / "tracker.sqlite3"
        backup_database(source, args.destination)
        print("Резервну копію SQLite збережено. Конфігурацію env.json копіюйте окремо захищеним способом.")
    else:
        bundle()


if __name__ == "__main__":
    main()
