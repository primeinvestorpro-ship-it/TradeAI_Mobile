"""
position_guardian.py — Live Kite Position Monitor, MTM Stop-Loss & Trailing SL Guardian.

Monitors:
  1. All existing open positions in Zerodha Kite (including positions placed manually
     on Kite web/mobile or from earlier in the day).
  2. All positions triggered via the dashboard interaction panel (/api/scanner/order),
     including Dry-Run simulated positions when dry_run=True.

Protections:
  - Combined MTM Stop Loss (auto square-off all when open MTM <= -max_loss)
  - MTM Trailing Stop Loss (ratchets up SL as peak MTM grows; locks in profits)
  - Per-Position Stop Loss % (protects individual option/futures legs from sudden spikes)
  - Optional MTM Target Profit booking
  - 1-click Emergency Square-Off All & Per-Position Exit
"""

from __future__ import annotations

import datetime
import threading
import time
from typing import Any, Callable, Dict, List, Optional


DEFAULT_GUARDIAN_CONFIG: Dict[str, Any] = {
    "mtm_sl_enabled": True,
    "max_loss": 4000.0,
    "trailing_sl_enabled": True,
    "trailing_activation": 1500.0,
    "trailing_gap": 750.0,
    "per_pos_sl_enabled": True,
    "per_pos_sl_pct": 25.0,
    "target_enabled": False,
    "target_profit": 8000.0,
}


class PositionGuardian:
    """Background guardian that continuously monitors Kite & simulated positions."""

    def __init__(
        self,
        kite_client: Any,
        settings_loader: Callable[[], Dict[str, Any]],
        settings_saver: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_update: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_alert: Optional[Callable[[Dict[str, Any]], None]] = None,
        log: Callable[[str], None] = print,
        poll_interval_sec: float = 2.0,
    ) -> None:
        self.kite_client = kite_client
        self.settings_loader = settings_loader
        self.settings_saver = settings_saver
        self.on_update = on_update
        self.on_alert = on_alert
        self.log = log
        self.poll_interval_sec = poll_interval_sec

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # Live MTM & Trailing SL state
        self._positions: List[Dict[str, Any]] = []
        self._sim_positions: Dict[str, Dict[str, Any]] = {}  # key: "EXCHANGE:SYMBOL:PRODUCT"
        self._peak_mtm: float = 0.0
        self._current_mtm: float = 0.0
        self._day_total_pnl: float = 0.0
        self._trailing_active: bool = False
        self._trailing_sl_level: Optional[float] = None
        self._pos_peaks: Dict[str, float] = {}  # key -> peak pnl
        self._last_trigger: Optional[Dict[str, Any]] = None
        self._squaring_off_keys: set[str] = set()
        self._last_poll_ts: Optional[str] = None
        self._last_error: Optional[str] = None
        self._history_events: List[Dict[str, Any]] = []

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(
                target=self._loop, name="PositionGuardian", daemon=True
            )
            self._thread.start()
            self.log("[guardian] Position MTM & Trailing SL Guardian started.")

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self.log("[guardian] Position Guardian stopped.")

    def is_running(self) -> bool:
        return self._running

    # ── Config helpers ────────────────────────────────────────────────────────

    def get_config(self) -> Dict[str, Any]:
        settings = self.settings_loader() or {}
        cfg = dict(DEFAULT_GUARDIAN_CONFIG)
        # Use explicit mtm_sl_enabled if configured; default to True for safety
        if "mtm_sl_enabled" in settings:
            cfg["mtm_sl_enabled"] = bool(settings["mtm_sl_enabled"])
        else:
            cfg["mtm_sl_enabled"] = True
        if "max_loss" in settings:
            try:
                cfg["max_loss"] = abs(float(settings["max_loss"]))
            except (TypeError, ValueError):
                pass
        if "trailing_sl_enabled" in settings:
            cfg["trailing_sl_enabled"] = bool(settings["trailing_sl_enabled"])
        if "trailing_activation" in settings:
            try:
                cfg["trailing_activation"] = max(100.0, float(settings["trailing_activation"]))
            except (TypeError, ValueError):
                pass
        if "trailing_gap" in settings:
            try:
                cfg["trailing_gap"] = max(50.0, float(settings["trailing_gap"]))
            except (TypeError, ValueError):
                pass
        if "per_pos_sl_enabled" in settings:
            cfg["per_pos_sl_enabled"] = bool(settings["per_pos_sl_enabled"])
        if "per_pos_sl_pct" in settings:
            try:
                cfg["per_pos_sl_pct"] = max(1.0, min(95.0, float(settings["per_pos_sl_pct"])))
            except (TypeError, ValueError):
                pass
        if "target_enabled" in settings:
            cfg["target_enabled"] = bool(settings["target_enabled"])
        if "target_profit" in settings:
            try:
                cfg["target_profit"] = abs(float(settings["target_profit"]))
            except (TypeError, ValueError):
                pass
        cfg["dry_run"] = bool(settings.get("dry_run", True))
        return cfg

    def update_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        settings = self.settings_loader() or {}
        for bool_k in ("mtm_sl_enabled", "trailing_sl_enabled", "per_pos_sl_enabled", "target_enabled"):
            if bool_k in updates:
                settings[bool_k] = bool(updates[bool_k])
                if bool_k == "mtm_sl_enabled":
                    settings["sl_enabled"] = bool(updates[bool_k])
        for num_k in ("max_loss", "trailing_activation", "trailing_gap", "per_pos_sl_pct", "target_profit"):
            if num_k in updates:
                try:
                    settings[num_k] = float(updates[num_k])
                except (TypeError, ValueError):
                    pass

        if self.settings_saver:
            self.settings_saver(settings)

        # Re-evaluate trailing SL floor immediately if config tightened
        with self._lock:
            cfg = self.get_config()
            if cfg["trailing_sl_enabled"] and self._trailing_active:
                new_floor = round(self._peak_mtm - cfg["trailing_gap"], 2)
                if self._trailing_sl_level is None or new_floor > self._trailing_sl_level:
                    self._trailing_sl_level = new_floor
            elif not cfg["trailing_sl_enabled"]:
                self._trailing_active = False
                self._trailing_sl_level = None

        self._record_event("CONFIG_UPDATED", "Risk protection rules updated.")
        return self.get_state()

    # ── Simulated / Order Hook ────────────────────────────────────────────────

    def register_order(
        self,
        tradingsymbol: str,
        exchange: str,
        transaction_type: str,
        quantity: int,
        product: str,
        dry_run: bool = False,
        entry_price: Optional[float] = None,
    ) -> None:
        """
        Called immediately whenever an order is triggered via the interaction panel
        (/api/scanner/order). Tracks simulated positions in dry_run mode and ensures
        the guardian is active and armed.
        """
        if not self._running:
            self.start()

        key = f"{exchange}:{tradingsymbol}:{product}"
        signed_qty = int(quantity) if transaction_type == "BUY" else -int(quantity)

        if dry_run:
            # Fetch current market LTP for realistic simulation entry price
            ltp = entry_price
            if ltp is None or ltp <= 0:
                try:
                    q = self.kite_client.get_quote([f"{exchange}:{tradingsymbol}"])
                    ltp = float(q.get(f"{exchange}:{tradingsymbol}", {}).get("last_price") or 0.0)
                except Exception:
                    ltp = 0.0

            with self._lock:
                existing = self._sim_positions.get(key)
                if existing:
                    old_qty = existing["quantity"]
                    new_qty = old_qty + signed_qty
                    if new_qty == 0:
                        self._sim_positions.pop(key, None)
                    else:
                        if (old_qty > 0 and signed_qty > 0) or (old_qty < 0 and signed_qty < 0):
                            total_cost = (abs(old_qty) * existing["average_price"]) + (abs(signed_qty) * ltp)
                            existing["average_price"] = round(total_cost / abs(new_qty), 2)
                        existing["quantity"] = new_qty
                        existing["last_price"] = ltp
                else:
                    self._sim_positions[key] = {
                        "tradingsymbol": tradingsymbol,
                        "exchange": exchange,
                        "product": product,
                        "quantity": signed_qty,
                        "average_price": round(ltp, 2),
                        "last_price": round(ltp, 2),
                        "pnl": 0.0,
                        "simulated": True,
                    }

        self._record_event(
            "ORDER_ARMED",
            f"{'[SIM] ' if dry_run else ''}Armed SL & Trailing SL protection for {transaction_type} {quantity} x {tradingsymbol}",
        )
        # Run an immediate poll cycle so UI updates instantly
        try:
            self.poll_once()
        except Exception:
            pass

    # ── Core Polling & Risk Check ─────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._last_error = str(exc)
                self.log(f"[guardian] Poll error: {exc!r}")
            self._stop_event.wait(self.poll_interval_sec)

    def poll_once(self) -> Dict[str, Any]:
        cfg = self.get_config()
        open_positions: List[Dict[str, Any]] = []
        day_total_pnl = 0.0

        # 1. Fetch real Kite positions
        try:
            pos_resp = self.kite_client.kite.positions()
            net_list = pos_resp.get("net", []) if isinstance(pos_resp, dict) else []
            for p in net_list:
                qty = int(p.get("quantity") or 0)
                day_total_pnl += float(p.get("pnl") or 0.0)
                if qty == 0:
                    continue
                sym = p.get("tradingsymbol", "")
                exch = p.get("exchange", "NFO")
                prod = p.get("product", "MIS")
                avg = float(p.get("average_price") or (p.get("buy_price") if qty > 0 else p.get("sell_price")) or 0.0)
                ltp = float(p.get("last_price") or avg or 0.0)
                open_mtm = round((ltp - avg) * qty, 2) if avg > 0 else float(p.get("unrealised") or p.get("pnl") or 0.0)
                entry_val = abs(qty) * avg
                pnl_pct = round((open_mtm / entry_val) * 100.0, 2) if entry_val > 0 else 0.0

                sl_price = None
                if cfg["per_pos_sl_enabled"] and avg > 0:
                    sl_factor = cfg["per_pos_sl_pct"] / 100.0
                    sl_price = round(avg * (1.0 - sl_factor) if qty > 0 else avg * (1.0 + sl_factor), 2)

                open_positions.append({
                    "key": f"{exch}:{sym}:{prod}",
                    "tradingsymbol": sym,
                    "exchange": exch,
                    "product": prod,
                    "quantity": qty,
                    "side": "LONG" if qty > 0 else "SHORT",
                    "average_price": round(avg, 2),
                    "last_price": round(ltp, 2),
                    "pnl": open_mtm,
                    "total_contract_pnl": round(float(p.get("pnl") or open_mtm), 2),
                    "pnl_pct": pnl_pct,
                    "sl_price": sl_price,
                    "simulated": False,
                })
        except Exception as exc:
            with self._lock:
                self._last_error = f"Kite positions fetch note: {exc}"

        # 2. Include simulated positions if dry_run and any exist
        with self._lock:
            sim_items = list(self._sim_positions.values())
        if sim_items:
            quote_keys = [f"{sp['exchange']}:{sp['tradingsymbol']}" for sp in sim_items]
            quotes = {}
            try:
                quotes = self.kite_client.get_quote(quote_keys) or {}
            except Exception:
                quotes = {}
            for sp in sim_items:
                q_key = f"{sp['exchange']}:{sp['tradingsymbol']}"
                ltp = float(quotes.get(q_key, {}).get("last_price") or sp.get("last_price") or sp.get("average_price") or 0.0)
                avg = float(sp.get("average_price") or ltp)
                qty = int(sp["quantity"])
                open_mtm = round((ltp - avg) * qty, 2)
                entry_val = abs(qty) * avg
                pnl_pct = round((open_mtm / entry_val) * 100.0, 2) if entry_val > 0 else 0.0
                sl_price = None
                if cfg["per_pos_sl_enabled"] and avg > 0:
                    sl_factor = cfg["per_pos_sl_pct"] / 100.0
                    sl_price = round(avg * (1.0 - sl_factor) if qty > 0 else avg * (1.0 + sl_factor), 2)
                open_positions.append({
                    "key": f"{sp['exchange']}:{sp['tradingsymbol']}:{sp['product']}:SIM",
                    "tradingsymbol": sp["tradingsymbol"],
                    "exchange": sp["exchange"],
                    "product": sp["product"],
                    "quantity": qty,
                    "side": "LONG" if qty > 0 else "SHORT",
                    "average_price": round(avg, 2),
                    "last_price": round(ltp, 2),
                    "pnl": open_mtm,
                    "total_contract_pnl": open_mtm,
                    "pnl_pct": pnl_pct,
                    "sl_price": sl_price,
                    "simulated": True,
                })

        # 3. Compute Aggregate MTM & Evaluate Trailing SL / MTM SL / Per-Pos SL
        current_mtm = round(sum(p["pnl"] for p in open_positions), 2)
        triggers_to_execute: List[Dict[str, Any]] = []

        with self._lock:
            self._positions = open_positions
            self._current_mtm = current_mtm
            self._day_total_pnl = round(day_total_pnl, 2)
            self._last_poll_ts = datetime.datetime.now().isoformat()
            self._last_error = None

            if not open_positions:
                # Reset trailing state when flat
                self._peak_mtm = 0.0
                self._trailing_active = False
                self._trailing_sl_level = None
                self._pos_peaks.clear()
            else:
                # Update peak MTM
                if current_mtm > self._peak_mtm:
                    self._peak_mtm = current_mtm

                # Update Trailing SL level
                if cfg["trailing_sl_enabled"]:
                    if self._peak_mtm >= cfg["trailing_activation"]:
                        if not self._trailing_active:
                            self._trailing_active = True
                            self._record_event(
                                "TRAILING_ACTIVATED",
                                f"Trailing SL activated at MTM ₹{current_mtm:,.2f} (Peak: ₹{self._peak_mtm:,.2f})",
                            )
                        candidate_floor = round(self._peak_mtm - cfg["trailing_gap"], 2)
                        if self._trailing_sl_level is None or candidate_floor > self._trailing_sl_level:
                            self._trailing_sl_level = candidate_floor

                # Check Per-Position Peak & SL %
                for p in open_positions:
                    pk = p["key"]
                    self._pos_peaks[pk] = max(self._pos_peaks.get(pk, p["pnl"]), p["pnl"])
                    if cfg["per_pos_sl_enabled"] and p["pnl_pct"] <= -abs(cfg["per_pos_sl_pct"]):
                        triggers_to_execute.append({
                            "type": "PER_POSITION_SL",
                            "position": p,
                            "reason": f"{p['tradingsymbol']} hit {p['pnl_pct']:.1f}% SL (limit -{cfg['per_pos_sl_pct']:.1f}%, P&L ₹{p['pnl']:,.2f})",
                        })

                # Check Trailing SL Hit (Combined)
                if cfg["trailing_sl_enabled"] and self._trailing_active and self._trailing_sl_level is not None:
                    if current_mtm <= self._trailing_sl_level:
                        triggers_to_execute.append({
                            "type": "TRAILING_SL_HIT",
                            "reason": f"MTM Trailing SL Hit! Current MTM ₹{current_mtm:,.2f} <= Trailing Floor ₹{self._trailing_sl_level:,.2f} (Peak was ₹{self._peak_mtm:,.2f})",
                        })

                # Check Combined MTM Max Loss SL Hit
                if cfg["mtm_sl_enabled"] and current_mtm <= -abs(cfg["max_loss"]):
                    triggers_to_execute.append({
                        "type": "MTM_SL_HIT",
                        "reason": f"MTM Stop Loss Hit! Current MTM ₹{current_mtm:,.2f} <= -₹{abs(cfg['max_loss']):,.2f}",
                    })

                # Check Combined MTM Target Profit
                if cfg["target_enabled"] and current_mtm >= abs(cfg["target_profit"]):
                    triggers_to_execute.append({
                        "type": "TARGET_PROFIT_HIT",
                        "reason": f"MTM Target Hit! Current MTM ₹{current_mtm:,.2f} >= +₹{abs(cfg['target_profit']):,.2f}",
                    })

        # 4. Execute square-offs outside lock
        for trig in triggers_to_execute:
            if trig["type"] == "PER_POSITION_SL":
                self.square_off_position(trig["position"], reason=trig["reason"])
            else:
                self.square_off_all(reason=trig["reason"], trigger_type=trig["type"])
                break  # All positions squared off

        state = self.get_state()
        if self.on_update:
            try:
                self.on_update(state)
            except Exception:
                pass
        return state

    # ── Square-Off Actions ────────────────────────────────────────────────────

    def square_off_position(self, pos: Dict[str, Any], reason: str = "MANUAL_EXIT") -> Dict[str, Any]:
        key = pos["key"]
        with self._lock:
            if key in self._squaring_off_keys:
                return {"ok": False, "error": "Square-off already in progress"}
            self._squaring_off_keys.add(key)

        try:
            qty = int(pos["quantity"])
            if qty == 0:
                return {"ok": True, "message": "Already flat"}
            exit_side = "SELL" if qty > 0 else "BUY"
            exit_qty = abs(qty)
            sym = pos["tradingsymbol"]
            exch = pos["exchange"]
            prod = pos["product"]

            if pos.get("simulated"):
                sim_key = f"{exch}:{sym}:{prod}"
                with self._lock:
                    self._sim_positions.pop(sim_key, None)
                res = {"dry_run": True, "simulated_exit": True, "tradingsymbol": sym, "quantity": exit_qty, "side": exit_side}
            else:
                # Real Kite exit — verify actual live qty right before order to avoid flipping
                actual_qty = self.kite_client.get_actual_position_qty(sym, exchange=exch, product=prod, expected_qty=qty)
                if actual_qty == 0:
                    return {"ok": True, "message": f"{sym} already flat in Kite"}
                real_side = "SELL" if actual_qty > 0 else "BUY"
                real_qty = abs(int(actual_qty))
                res = self.kite_client.place_market_order(
                    tradingsymbol=sym,
                    exchange=exch,
                    transaction_type=real_side,
                    quantity=real_qty,
                    product=prod,
                )

            self.log(f"[guardian] 🚨 EXITED {sym} ({exit_side} {exit_qty}) | Reason: {reason}")
            self._record_event("POSITION_EXITED", f"{sym} exited ({exit_side} {exit_qty}) — {reason}")
            alert = {"type": "POSITION_EXITED", "symbol": sym, "reason": reason, "result": res, "timestamp": datetime.datetime.now().isoformat()}
            with self._lock:
                self._last_trigger = alert
            if self.on_alert:
                try:
                    self.on_alert(alert)
                except Exception:
                    pass
            return {"ok": True, "result": res}
        finally:
            with self._lock:
                self._squaring_off_keys.discard(key)

    def square_off_all(self, reason: str = "MANUAL_SQUARE_OFF_ALL", trigger_type: str = "MANUAL_EXIT") -> Dict[str, Any]:
        with self._lock:
            positions_copy = list(self._positions)

        if not positions_copy:
            return {"ok": True, "exited": 0, "message": "No open positions to exit."}

        results = []
        for p in positions_copy:
            r = self.square_off_position(p, reason=reason)
            results.append({"symbol": p["tradingsymbol"], "result": r})

        self._record_event(trigger_type, f"Squared off {len(results)} position(s) — {reason}")
        alert = {
            "type": trigger_type,
            "reason": reason,
            "exited_count": len(results),
            "results": results,
            "timestamp": datetime.datetime.now().isoformat(),
        }
        with self._lock:
            self._last_trigger = alert
        if self.on_alert:
            try:
                self.on_alert(alert)
            except Exception:
                pass
        return {"ok": True, "exited": len(results), "results": results}

    # ── State Snapshot ────────────────────────────────────────────────────────

    def _record_event(self, event_type: str, message: str) -> None:
        entry = {
            "time": datetime.datetime.now().strftime("%H:%M:%S"),
            "type": event_type,
            "message": message,
        }
        self._history_events.insert(0, entry)
        if len(self._history_events) > 25:
            self._history_events = self._history_events[:25]

    def get_state(self) -> Dict[str, Any]:
        cfg = self.get_config()
        with self._lock:
            effective_sl = None
            if cfg["trailing_sl_enabled"] and self._trailing_active and self._trailing_sl_level is not None:
                effective_sl = self._trailing_sl_level
            elif cfg["mtm_sl_enabled"]:
                effective_sl = -abs(cfg["max_loss"])

            return {
                "running": self._running,
                "config": cfg,
                "positions": list(self._positions),
                "open_count": len(self._positions),
                "current_mtm": self._current_mtm,
                "day_total_pnl": self._day_total_pnl,
                "peak_mtm": self._peak_mtm,
                "trailing_active": self._trailing_active,
                "trailing_sl_level": self._trailing_sl_level,
                "effective_sl": effective_sl,
                "last_trigger": self._last_trigger,
                "last_poll_ts": self._last_poll_ts,
                "last_error": self._last_error,
                "events": list(self._history_events[:10]),
            }
