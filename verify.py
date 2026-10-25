"""Відтворювана перевірка обох штучних виписок: uv run python verify.py."""
from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from report import load_report

bank_import = importlib.import_module("import")
ROOT = Path(__file__).resolve().parent
EXPECTED = {
    "ola": {8: "2195.21", 9: "2582.42"},
    "marko": {8: "1855.57", 9: "2432.83"},
}


def verify_examples() -> dict:
    manifest = json.loads((ROOT / "examples/manifest.json").read_text())
    for name, expected in manifest["files"].items():
        actual = "sha256:" + hashlib.sha256((ROOT / "examples" / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Змінено контрольне джерело {name}")
    with tempfile.TemporaryDirectory(prefix="finance-verify-") as directory:
        temp = Path(directory)
        inputs = temp / "inputs"
        inputs.mkdir()
        (inputs / "ola.csv").write_bytes((ROOT / "examples/ola-statement.csv").read_bytes())
        rows = bank_import.ola_rows(inputs)
        rows += bank_import.marko_rows(ROOT / "examples/marko-statement.csv")
        bank_import.write_ledger(rows, bank_import.load_rules(), temp / "transactions.beancount")
        main = temp / "main.beancount"
        main.write_text((ROOT / "main.beancount").read_text())
        result = subprocess.run(
            ["uv", "run", "--offline", "--no-sync", "bean-check", str(main)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        if result.returncode or result.stdout or result.stderr:
            raise ValueError("bean-check не пройшов без повідомлень:\n" + result.stdout + result.stderr)
        output = {}
        for owner, control in EXPECTED.items():
            months = load_report(owner, main)
            for month, expected in control.items():
                record = next(r for r in months if r["year"] == 2026 and r["month"] == month)
                if record["variable_pln"] != bank_import.Decimal(expected):
                    raise ValueError(f"Контроль {owner} за 2026-{month:02d} не збігається")
                output[f"{owner}:2026-{month:02d}"] = record["variable_pln"]
        return output


def main() -> None:
    values = verify_examples()
    print("bean-check: код 0, без повідомлень. Джерела SHA-256 незмінені.")
    for key, value in values.items():
        print(f"{key}: змінні витрати {value:.2f} PLN — OK")
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
