from __future__ import annotations

import csv
import importlib
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

import report
import verify

bank = importlib.import_module("import")
ROOT = Path(__file__).resolve().parents[1]


class BankImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        (self.directory / "ola.csv").write_bytes((ROOT / "examples/ola-statement.csv").read_bytes())

    def rows(self):
        return bank.ola_rows(self.directory) + bank.marko_rows(ROOT / "examples/marko-statement.csv")

    def test_full_control_check(self):
        result = verify.verify_examples()
        self.assertEqual(result, {"ola:2026-08": Decimal("2195.21"),
                                  "ola:2026-09": Decimal("2582.42"),
                                  "marko:2026-08": Decimal("1855.57"),
                                  "marko:2026-09": Decimal("2432.83")})

    def test_repeat_file_does_not_duplicate_rows_or_change_journal(self):
        output = self.directory / "generated.beancount"
        bank.write_ledger(self.rows(), bank.load_rules(), output)
        first = output.read_bytes()
        (self.directory / "copy.csv").write_bytes((self.directory / "ola.csv").read_bytes())
        self.assertEqual(len(bank.ola_rows(self.directory)), 142)
        bank.write_ledger(self.rows(), bank.load_rules(), output)
        self.assertEqual(output.read_bytes(), first)

    def test_marko_preserves_bank_sequence_and_final_balance(self):
        rows = bank.marko_rows(ROOT / "examples/marko-statement.csv")
        self.assertEqual(len(rows), 109)
        bank.validate_rows(rows)
        self.assertEqual(rows[-1].balance, Decimal("11212.47"))
        self.assertEqual(rows[0].balance, Decimal("2835.76"))

    def test_bank_error_keeps_previous_valid_output(self):
        output = self.directory / "generated.beancount"
        output.write_text("previous valid journal")
        rows = self.rows()
        rows[0] = replace(rows[0], balance=rows[0].balance + Decimal("0.01"))
        with self.assertRaisesRegex(ValueError, "Залишок"):
            bank.write_ledger(rows, bank.load_rules(), output)
        self.assertEqual(output.read_text(), "previous valid journal")

    def test_currency_and_out_of_order_rows_are_rejected(self):
        rows = self.rows()
        with self.assertRaisesRegex(ValueError, "PLN"):
            bank.validate_rows([replace(rows[0], currency="EUR")])
        with self.assertRaises(ValueError):
            bank.validate_rows(list(reversed(rows)))

    def test_ids_separate_owners_and_normalize_money(self):
        row = self.rows()[0]
        self.assertNotEqual(row.bank_id, replace(row, owner="marko").bank_id)
        self.assertEqual(bank.money("2,5"), bank.money("2.50"))
        for value in ["NaN", "Infinity", "1.001"]:
            with self.assertRaises(ValueError):
                bank.money(value)

    def test_fixed_rules_are_from_rules_csv(self):
        rules = bank.load_rules()
        for description in ["ORANGE POLSKA", "PGE OBRÓT", "ZDROFIT", "GOOGLE *YOUTUBE PREMIUM"]:
            _, fixed = bank.match_rule(description, Decimal("-1"), rules)
            self.assertTrue(fixed, description)
        self.assertFalse(bank.match_rule("KAUFLAND", Decimal("-1"), rules)[1])

    def test_owner_is_allowlisted(self):
        with self.assertRaises(ValueError):
            report.load_report("anyone")

    def test_different_decimal_spelling_does_not_duplicate(self):
        path = self.directory / "ola.csv"
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        duplicate = dict(rows[0])
        duplicate["Amount"] = str(Decimal(duplicate["Amount"]).quantize(Decimal("0.001")))
        rows.append(duplicate)
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
        self.assertEqual(len(bank.ola_rows(self.directory)), 142)


if __name__ == "__main__":
    unittest.main()
