from __future__ import annotations

from src.paper.account import PaperAccount


def test_paper_account_initializes_from_config():
    account = PaperAccount.from_config({"paper_account": {"initial_cash": 12345}})
    assert account.state()["cash"] == 12345
    assert account.state()["mode"] == "paper_trading"
