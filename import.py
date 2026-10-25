"""Імпорт банківських CSV у transactions.beancount. Файл повністю перезаписується."""

from __future__ import annotations

import csv
import argparse
import hashlib
import io
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RULES_PATH = ROOT / "rules.csv"
OUTPUT_PATH = ROOT / "transactions.beancount"
BANK = "Assets:Банк"
BANKS = {"ola": BANK, "marko": "Assets:Банк-Марко"}
OPENING = {"ola": Decimal("4500.00"), "marko": Decimal("2860.40")}
UNCATEGORIZED = "Expenses:Без-Категорії"
OTHER_INCOME = "Income:Інше"


def load_rules() -> list[tuple[str, str, bool]]:
    rules: list[tuple[str, str, bool]] = []
    with RULES_PATH.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            pattern = (row.get("шаблон") or "").strip()
            account = (row.get("рахунок") or "").strip()
            if not pattern or not account:
                continue
            fixed = (row.get("фіксована") or "").strip().lower() == "так"
            rules.append((pattern, account, fixed))
    return rules


def match_rule(
    description: str, amount: Decimal, rules: list[tuple[str, str, bool]]
) -> tuple[str, bool]:
    haystack = description.casefold()
    for pattern, account, fixed in rules:
        if pattern.casefold() in haystack and (
            amount <= 0 or account.startswith("Income:")
        ):
            return account, fixed
    if amount > 0:
        return OTHER_INCOME, False
    return UNCATEGORIZED, False


def parse_completed(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")


def escape_beancount(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def load_rows(directory: Path | None = None) -> list[dict]:
    files = sorted((directory or DATA_DIR).glob("*.csv"))
    seen: set[tuple[str, str, str, str]] = set()
    rows: list[dict] = []
    for path in files:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for raw in reader:
                completed = raw["Completed Date"].strip()
                description = raw["Description"].strip()
                amount = raw["Amount"].strip()
                balance = raw["Balance"].strip()
                key = (completed, description, amount, balance)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(raw)
    return rows


def render_transaction(
    date_str: str,
    description: str,
    amount: Decimal,
    currency: str,
    account: str,
    fixed: bool,
    owner: str = "ola",
    bank_id: str = "",
) -> str:
    if owner not in BANKS:
        raise ValueError("Невідомий власник")
    tag = f" #{owner}" + (" #fixed" if fixed else "")
    narration = escape_beancount(description)
    bank_amount = f"{amount:.2f}"
    other_amount = f"{-amount:.2f}"
    lines = [
        f'{date_str} * "{narration}"{tag}',
        *([f'  bank_id: "{bank_id}"'] if bank_id else []),
        f"  {BANKS[owner]}  {bank_amount} {currency}",
        f"  {account}  {other_amount} {currency}",
        "",
    ]
    return "\n".join(lines)


def first_of_next_month(year: int, month: int):
    if month == 12:
        return datetime(year + 1, 1, 1).date()
    return datetime(year, month + 1, 1).date()


@dataclass(frozen=True)
class BankRow:
    owner: str
    completed: datetime
    description: str
    amount: Decimal
    balance: Decimal
    currency: str = "PLN"

    @property
    def key(self) -> tuple:
        return (self.owner, self.completed, self.description, self.amount, self.balance)

    @property
    def bank_id(self) -> str:
        # Include owner: identical rows from different banks are distinct facts.
        return hashlib.sha256(repr(self.key).encode()).hexdigest()


def money(value: str) -> Decimal:
    number = Decimal(value.replace("\u00a0", "").replace(" ", "").replace(",", "."))
    if not number.is_finite() or number != number.quantize(Decimal("0.01")):
        raise ValueError("Очікується скінченна сума з точністю до гроша")
    return number.quantize(Decimal("0.01"))


def ola_rows(directory: Path) -> list[BankRow]:
    rows = [BankRow("ola", parse_completed(r["Completed Date"]),
                    r["Description"].strip(), money(r["Amount"]),
                    money(r["Balance"]), r["Currency"].strip())
            for r in load_rows(directory)]
    # A changed decimal spelling (2.5 / 2.50) is still the same bank fact.
    return list({row.key: row for row in rows}.values())


def marko_rows(path: Path) -> list[BankRow]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    header = next((i for i, line in enumerate(lines)
                   if line.startswith("Data operacji;Data księgowania;")), None)
    if header is None:
        raise ValueError("Немає заголовка польської виписки")
    metadata = dict(line.split(";", 1) for line in lines[:header] if ";" in line)
    if metadata.get("Waluta") != "PLN":
        raise ValueError("Підтримується лише PLN")
    if money(metadata.get("Saldo początkowe", "NaN")) != OPENING["marko"]:
        raise ValueError("Початковий залишок Марка не відповідає відкриттю рахунку")
    rows = []
    seen = set()
    for r in csv.DictReader(io.StringIO("\n".join(lines[header:])), delimiter=";"):
        row = BankRow("marko", datetime.strptime(r["Data księgowania"], "%d.%m.%Y"),
                      r["Opis"].strip(), money(r["Kwota"]), money(r["Saldo po operacji"]))
        if row.key not in seen:
            seen.add(row.key)
            rows.append(row)
    if not rows:
        raise ValueError("Немає операцій Марка")
    # Bank exports newest first. Reverse the entire bank sequence; never sort
    # individual rows by the displayed time/date within a day.
    return list(reversed(rows))


def validate_rows(rows: list[BankRow]) -> None:
    for owner in BANKS:
        previous = OPENING[owner]
        last_date = None
        for row in (r for r in rows if r.owner == owner):
            if row.currency != "PLN":
                raise ValueError("Підтримується лише PLN")
            if last_date is not None and row.completed.date() < last_date:
                raise ValueError("Порушено банківський порядок; перевірте порядок вхідних файлів")
            if previous + row.amount != row.balance:
                raise ValueError(f"Залишок {owner} не збігається з банком на {row.completed.date()}")
            previous, last_date = row.balance, row.completed.date()


def write_ledger(rows: list[BankRow], rules: list[tuple[str, str, bool]],
                 output: Path | None = None) -> None:
    validate_rows(rows)
    output = output or OUTPUT_PATH
    if output.resolve().is_relative_to(DATA_DIR.resolve()):
        raise ValueError("Заборонено писати результати у data/")
    chunks: list[str] = [
        "; Згенеровано import.py. Не редагувати руками.\n\n",
    ]
    last_by_month: dict[tuple[str, int, int], BankRow] = {}

    for row in rows:
        account, fixed = match_rule(row.description, row.amount, rules)
        date_str = row.completed.strftime("%Y-%m-%d")
        chunks.append(
            render_transaction(date_str, row.description, row.amount, row.currency,
                               account, fixed, row.owner, row.bank_id)
        )
        month = (row.owner, row.completed.year, row.completed.month)
        last_by_month[month] = row

    if last_by_month:
        for owner, year, month in sorted(last_by_month):
            row = last_by_month[(owner, year, month)]
            last_month = max((y, m) for o, y, m in last_by_month if o == owner)
            assert_on = ((row.completed + timedelta(days=1)).date()
                         if (year, month) == last_month else first_of_next_month(year, month))
            chunks.append(
                f"{assert_on.isoformat()} balance {BANKS[owner]}  {row.balance:.2f} PLN\n"
            )

    # Validate everything before replacing the last valid generated journal.
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".ledger-", delete=False) as fh:
            temporary = Path(fh.name)
            fh.write("".join(chunks)); fh.flush(); os.fsync(fh.fileno())
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ola-dir", type=Path,
                        default=DATA_DIR if DATA_DIR.exists() else ROOT / "examples")
    parser.add_argument("--marko", type=Path, default=ROOT / "examples/marko-statement.csv")
    args = parser.parse_args()
    rules = load_rules()
    # examples/ holds two different CSV formats; don't pass both to Ola's parser.
    if args.ola_dir.resolve() == (ROOT / "examples").resolve():
        with tempfile.TemporaryDirectory() as directory:
            source = ROOT / "examples/ola-statement.csv"
            Path(directory, "ola.csv").write_bytes(source.read_bytes())
            rows = ola_rows(Path(directory))
    else:
        rows = ola_rows(args.ola_dir)
    if not rows:
        raise ValueError("Немає операцій Олі")
    rows += marko_rows(args.marko)
    write_ledger(rows, rules)
    print(f"Імпортовано: Оля {sum(r.owner == 'ola' for r in rows)}, "
          f"Марко {sum(r.owner == 'marko' for r in rows)}. Залишки кожного рядка звірено.")


if __name__ == "__main__":
    main()
