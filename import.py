"""Імпорт банківських CSV у transactions.beancount. Файл повністю перезаписується."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RULES_PATH = ROOT / "rules.csv"
OUTPUT_PATH = ROOT / "transactions.beancount"
BANK = "Assets:Банк"
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


def load_rows() -> list[dict]:
    files = sorted(DATA_DIR.glob("*.csv"))
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
) -> str:
    tag = " #fixed" if fixed else ""
    narration = escape_beancount(description)
    bank_amount = f"{amount:.2f}"
    other_amount = f"{-amount:.2f}"
    lines = [
        f'{date_str} * "{narration}"{tag}',
        f"  {BANK}  {bank_amount} {currency}",
        f"  {account}  {other_amount} {currency}",
        "",
    ]
    return "\n".join(lines)


def first_of_next_month(year: int, month: int):
    if month == 12:
        return datetime(year + 1, 1, 1).date()
    return datetime(year, month + 1, 1).date()


def write_ledger(rows: list[dict], rules: list[tuple[str, str, bool]]) -> None:
    chunks: list[str] = [
        "; Згенеровано import.py. Не редагувати руками.\n\n",
    ]
    last_by_month: dict[tuple[int, int], tuple[datetime, Decimal, str]] = {}

    for raw in rows:
        completed = parse_completed(raw["Completed Date"])
        description = raw["Description"].strip()
        amount = Decimal(raw["Amount"])
        balance = Decimal(raw["Balance"])
        currency = raw["Currency"].strip() or "PLN"
        account, fixed = match_rule(description, amount, rules)
        date_str = completed.strftime("%Y-%m-%d")
        chunks.append(
            render_transaction(date_str, description, amount, currency, account, fixed)
        )
        month = (completed.year, completed.month)
        previous = last_by_month.get(month)
        # Файли можуть перекриватися. Раніша дата не замінює кінець місяця;
        # усередині дня перемагає останній рядок банку, а не пізніший час.
        if previous is None or completed.date() >= previous[0].date():
            last_by_month[month] = (completed, balance, currency)

    if last_by_month:
        months = sorted(last_by_month)
        last_year, last_month = months[-1]
        for year, month in months:
            completed, balance, currency = last_by_month[(year, month)]
            if (year, month) == (last_year, last_month):
                assert_on = (completed + timedelta(days=1)).date()
            else:
                assert_on = first_of_next_month(year, month)
            chunks.append(
                f"{assert_on.isoformat()} balance {BANK}  {balance:.2f} {currency}\n"
            )

    OUTPUT_PATH.write_text("".join(chunks), encoding="utf-8")


def main() -> None:
    rules = load_rules()
    rows = load_rows()
    write_ledger(rows, rules)


if __name__ == "__main__":
    main()
