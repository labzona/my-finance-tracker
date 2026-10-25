"""A small monthly/cash report inside Fava's existing UI."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from flask import current_app, request
from fava.ext import FavaExtensionBase


class Household(FavaExtensionBase):
    report_title = "Місячний звіт і готівка"

    def view(self):
        owner = current_app.config["TRACKER_OWNER"]
        store = current_app.config["TRACKER_STORE"]
        return {**store.monthly(owner, request.args.get("month")),
                "categories": store.categories, "owner": owner,
                "name": "Оля" if owner == "ola" else "Марко",
                "csrf": request.environ["tracker.csrf"],
                "nonce": request.environ["tracker.nonce"],
                "today": datetime.now(ZoneInfo("Europe/Warsaw")).date().isoformat()}

    @staticmethod
    def money(value):
        return f"{Decimal(value):,.2f}".replace(",", " ").replace(".", ",")

    def cents(self, value):
        return self.money(Decimal(value) / 100)
