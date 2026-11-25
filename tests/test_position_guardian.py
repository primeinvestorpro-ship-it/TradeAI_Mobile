"""
Unit tests for PositionGuardian (MTM Stop Loss, Trailing SL, Per-Position SL,
manual Kite positions, and interaction panel order registration).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from position_guardian import PositionGuardian


class MockKite:
    def __init__(self):
        self.net_positions = []
        self.orders_placed = []
        self.quotes = {}

    def positions(self):
        return {"net": list(self.net_positions)}


class MockKiteClient:
    def __init__(self, dry_run=False):
        self.kite = MockKite()
        self.settings = {"dry_run": dry_run}

    def get_quote(self, keys):
        return {k: self.kite.quotes.get(k, {"last_price": 0.0}) for k in keys}

    def get_actual_position_qty(self, tradingsymbol, exchange="NFO", product="MIS", expected_qty=None):
        for p in self.kite.net_positions:
            if p["tradingsymbol"] == tradingsymbol and p["exchange"] == exchange and p["product"] == product:
                return p["quantity"]
        return 0

    def place_market_order(self, tradingsymbol, exchange, transaction_type, quantity, product=None):
        self.kite.orders_placed.append({
            "tradingsymbol": tradingsymbol,
            "exchange": exchange,
            "transaction_type": transaction_type,
            "quantity": quantity,
            "product": product,
        })
        # Simulate closing the position in Kite
        for p in self.kite.net_positions:
            if p["tradingsymbol"] == tradingsymbol:
                p["quantity"] = 0
        return {"dry_run": False, "order_id": "MOCK_EXIT_123"}


def test_manual_kite_position_mtm_stop_loss():
    client = MockKiteClient(dry_run=False)
    settings = {
        "dry_run": False,
        "mtm_sl_enabled": True,
        "max_loss": 3000.0,
        "trailing_sl_enabled": False,
        "per_pos_sl_enabled": False,
    }
    # Existing manual Kite position: Long 60 qty @ 200, LTP drops to 140 => MTM = -3600 (<= -3000)
    client.kite.net_positions = [{
        "tradingsymbol": "FINNIFTY26DEC28850CE",
        "exchange": "NFO",
        "product": "MIS",
        "quantity": 60,
        "average_price": 200.0,
        "last_price": 140.0,
        "pnl": -3600.0,
    }]

    guardian = PositionGuardian(kite_client=client, settings_loader=lambda: settings, log=lambda m: None)
    st = guardian.poll_once()

    assert len(client.kite.orders_placed) == 1
    assert client.kite.orders_placed[0]["transaction_type"] == "SELL"
    assert client.kite.orders_placed[0]["quantity"] == 60
    assert st["last_trigger"]["type"] == "MTM_SL_HIT"


def test_mtm_trailing_stop_loss_ratchet_and_trigger():
    client = MockKiteClient(dry_run=False)
    settings = {
        "dry_run": False,
        "mtm_sl_enabled": True,
        "max_loss": 4000.0,
        "trailing_sl_enabled": True,
        "trailing_activation": 1500.0,
        "trailing_gap": 750.0,
        "per_pos_sl_enabled": False,
    }
    # Open Long 30 qty BANKNIFTY @ 400
    pos = {
        "tradingsymbol": "BANKNIFTY26NOV62400CE",
        "exchange": "NFO",
        "product": "MIS",
        "quantity": 30,
        "average_price": 400.0,
        "last_price": 460.0,  # +1800 MTM -> activates Trailing SL (floor = 1800 - 750 = +1050)
        "pnl": 1800.0,
    }
    client.kite.net_positions = [pos]
    guardian = PositionGuardian(kite_client=client, settings_loader=lambda: settings, log=lambda m: None)

    st1 = guardian.poll_once()
    assert st1["trailing_active"] is True
    assert st1["peak_mtm"] == 1800.0
    assert st1["trailing_sl_level"] == 1050.0
    assert len(client.kite.orders_placed) == 0

    # Profit rises to +3000 (LTP 500) -> floor ratchets up to +2250
    pos["last_price"] = 500.0
    st2 = guardian.poll_once()
    assert st2["peak_mtm"] == 3000.0
    assert st2["trailing_sl_level"] == 2250.0
    assert len(client.kite.orders_placed) == 0

    # Profit pulls back to +2100 (LTP 470) <= 2250 -> Trailing SL triggers!
    pos["last_price"] = 470.0
    st3 = guardian.poll_once()
    assert len(client.kite.orders_placed) == 1
    assert client.kite.orders_placed[0]["transaction_type"] == "SELL"
    assert st3["last_trigger"]["type"] == "TRAILING_SL_HIT"


def test_per_position_sl_pct_trigger():
    client = MockKiteClient(dry_run=False)
    settings = {
        "dry_run": False,
        "mtm_sl_enabled": True,
        "max_loss": 10000.0,  # High combined limit so per-pos SL triggers first
        "trailing_sl_enabled": False,
        "per_pos_sl_enabled": True,
        "per_pos_sl_pct": 25.0,
    }
    # Long 30 qty @ 200 (cost 6000), drops to 145 (-27.5% loss, -1650 MTM)
    client.kite.net_positions = [{
        "tradingsymbol": "NIFTY26NOV25800PE",
        "exchange": "NFO",
        "product": "MIS",
        "quantity": 30,
        "average_price": 200.0,
        "last_price": 145.0,
        "pnl": -1650.0,
    }]
    guardian = PositionGuardian(kite_client=client, settings_loader=lambda: settings, log=lambda m: None)
    st = guardian.poll_once()

    assert len(client.kite.orders_placed) == 1
    assert client.kite.orders_placed[0]["tradingsymbol"] == "NIFTY26NOV25800PE"
    assert st["last_trigger"]["type"] == "POSITION_EXITED"


def test_dry_run_interaction_panel_order_protection():
    client = MockKiteClient(dry_run=True)
    client.kite.quotes = {"NFO:FINNIFTY26DEC28850CE": {"last_price": 200.0}}
    settings = {
        "dry_run": True,
        "mtm_sl_enabled": True,
        "max_loss": 2000.0,
        "trailing_sl_enabled": True,
        "trailing_activation": 1000.0,
        "trailing_gap": 500.0,
        "per_pos_sl_enabled": True,
        "per_pos_sl_pct": 20.0,
    }
    guardian = PositionGuardian(kite_client=client, settings_loader=lambda: settings, log=lambda m: None)
    guardian.register_order("FINNIFTY26DEC28850CE", "NFO", "BUY", 60, "MIS", dry_run=True, entry_price=200.0)

    st1 = guardian.get_state()
    assert st1["open_count"] == 1
    assert st1["positions"][0]["sl_price"] == 160.0  # 20% below 200

    # Price drops to 150 (-25% <= -20% per-pos SL and -3000 <= -2000 MTM SL) -> simulated exit
    client.kite.quotes = {"NFO:FINNIFTY26DEC28850CE": {"last_price": 150.0}}
    st2 = guardian.poll_once()
    assert st2["last_trigger"] is not None
    # Simulated position should be closed
    st3 = guardian.poll_once()
    assert st3["open_count"] == 0
