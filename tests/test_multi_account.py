"""
Unit tests for Multi-Account Kite Support (account_manager, token files,
/api/accounts CRUD, parallel /api/scanner/order, and aggregated /api/guardian/status).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import account_manager
import app as flask_app
import kite_client


class _MockKite:
    def __init__(self, positions=None):
        self._positions = positions or []

    def positions(self):
        return {"net": list(self._positions)}


class _MockClient:
    def __init__(self, user_name="User", dry_run=True, positions=None):
        self._user_name = user_name
        self.settings = {"dry_run": dry_run}
        self.kite = _MockKite(positions)
        self.orders = []

    def place_market_order(self, tradingsymbol, exchange, transaction_type, quantity, product=None):
        self.orders.append((tradingsymbol, quantity, transaction_type, product))
        return {"dry_run": self.settings["dry_run"], "order_id": f"ORD_{self._user_name}", "tradingsymbol": tradingsymbol, "quantity": quantity}

    def get_quote(self, keys):
        out = {}
        for k in keys:
            sym = k.split(":", 1)[-1]
            p = next((x for x in self.kite._positions if x["tradingsymbol"] == sym), None)
            out[k] = {"last_price": p["last_price"] if p else 150.0}
        return out

    def get_actual_position_qty(self, tradingsymbol, exchange="NFO", product="MIS", expected_qty=None):
        return expected_qty or 0


def test_per_account_token_file_paths():
    assert kite_client.get_token_file(None).endswith("token.json")
    assert kite_client.get_token_file("primary").endswith("token.json")
    p2 = kite_client.get_token_file("acc_2")
    assert "tokens" in p2 and p2.endswith("acc_2.json")


def test_account_manager_crud(tmp_path, monkeypatch):
    tmp_acc_path = str(tmp_path / "accounts.json")
    monkeypatch.setattr(account_manager, "ACCOUNTS_PATH", tmp_acc_path)

    base_settings = {"kite_api_key": "k1", "kite_api_secret": "s1", "dry_run": True, "max_loss": 2500.0}
    loader = lambda: dict(base_settings)
    saver = lambda s: base_settings.update(s)

    accs = account_manager.load_accounts(loader)
    assert len(accs) == 1
    assert accs[0]["id"] == "primary"

    # Add second account
    acc2 = account_manager.add_or_update_account(
        {"label": "Second Kite", "api_key": "k2", "api_secret": "s2", "lot_multiplier": 2, "max_loss": 5000.0},
        loader,
        saver,
    )
    assert acc2["id"] == "acc_2"
    assert acc2["lot_multiplier"] == 2
    assert acc2["max_loss"] == 5000.0

    # Primary cannot be deleted, acc_2 can
    assert account_manager.delete_account("primary", loader) is False
    assert account_manager.delete_account("acc_2", loader) is True
    assert len(account_manager.load_accounts(loader)) == 1


def test_multi_account_order_and_guardian_aggregation(tmp_path, monkeypatch):
    tmp_acc_path = str(tmp_path / "accounts.json")
    monkeypatch.setattr(account_manager, "ACCOUNTS_PATH", tmp_acc_path)

    base_settings = {"kite_api_key": "k1", "kite_api_secret": "s1", "dry_run": True, "max_loss": 2500.0}
    monkeypatch.setattr(flask_app, "load_settings", lambda: dict(base_settings))
    monkeypatch.setattr(flask_app, "save_settings", lambda s: base_settings.update(s))

    account_manager.add_or_update_account(
        {"label": "Account 2", "api_key": "k2", "api_secret": "s2", "lot_multiplier": 2, "dry_run": True},
        flask_app.load_settings,
        flask_app.save_settings,
    )

    c1 = _MockClient("Ramesh", dry_run=True, positions=[{
        "tradingsymbol": "NIFTY26NOV25800CE", "exchange": "NFO", "product": "MIS",
        "quantity": 75, "average_price": 100.0, "last_price": 110.0, "pnl": 750.0,
    }])
    c2 = _MockClient("Account2", dry_run=True, positions=[{
        "tradingsymbol": "BANKNIFTY26NOV62400CE", "exchange": "NFO", "product": "MIS",
        "quantity": 30, "average_price": 400.0, "last_price": 420.0, "pnl": 600.0,
    }])

    with flask_app.STATE_LOCK:
        orig_accounts = dict(flask_app.STATE.get("accounts", {}))
        orig_client = flask_app.STATE.get("kite_client")
        orig_logged = flask_app.STATE.get("logged_in")
        flask_app.STATE["kite_client"] = c1
        flask_app.STATE["logged_in"] = True
        flask_app.STATE["accounts"] = {
            "primary": {"client": c1, "logged_in": True, "user_name": "Ramesh", "guardian": None},
            "acc_2": {"client": c2, "logged_in": True, "user_name": "Account2", "guardian": None},
        }

    try:
        client = flask_app.app.test_client()
        # Punch order to BOTH accounts
        r = client.post("/api/scanner/order", json={
            "tradingsymbol": "NIFTY26NOV25850CE",
            "exchange": "NFO",
            "transaction_type": "BUY",
            "quantity": 75,
            "product": "MIS",
            "account_ids": ["primary", "acc_2"],
            "use_multiplier": True,
        })
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True
        assert len(d["results"]) == 2
        assert c1.orders[0][1] == 75
        assert c2.orders[0][1] == 150  # 2x lot_multiplier!

        # Check combined guardian status
        rg = client.get("/api/guardian/status?account_id=all")
        assert rg.status_code == 200
        gd = rg.get_json()
        assert gd["ok"] is True
        assert len(gd["accounts"]) == 2
        assert gd["combined"]["current_mtm"] == 1350.0  # 750 + 600
    finally:
        # Clean up guardians & restore state
        with flask_app.STATE_LOCK:
            for v in flask_app.STATE.get("accounts", {}).values():
                if v.get("guardian"):
                    v["guardian"].stop()
            flask_app.STATE["accounts"] = orig_accounts
            flask_app.STATE["kite_client"] = orig_client
            flask_app.STATE["logged_in"] = orig_logged
