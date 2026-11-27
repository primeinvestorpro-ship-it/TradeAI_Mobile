"""
account_manager.py — Multi-Account Zerodha Kite Registry & Risk Settings Manager.

Stores secondary Kite Connect accounts in `accounts.json` while keeping the
primary account (`id="primary"`) synced with `settings.json` and `token.json`
for full backward compatibility.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Callable, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_PATH = os.path.join(BASE_DIR, "accounts.json")
_ACCOUNTS_LOCK = threading.Lock()


def _default_primary_from_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": "primary",
        "label": "Primary Kite",
        "api_key": settings.get("kite_api_key", ""),
        "api_secret": settings.get("kite_api_secret", ""),
        "lot_multiplier": 1,
        "dry_run": bool(settings.get("dry_run", True)),
        "mtm_sl_enabled": bool(settings.get("mtm_sl_enabled", True)),
        "max_loss": float(settings.get("max_loss", 2500.0)),
        "trailing_sl_enabled": bool(settings.get("trailing_sl_enabled", True)),
        "trailing_activation": float(settings.get("trailing_activation", 1500.0)),
        "trailing_gap": float(settings.get("trailing_gap", 750.0)),
        "per_pos_sl_enabled": bool(settings.get("per_pos_sl_enabled", True)),
        "per_pos_sl_pct": float(settings.get("per_pos_sl_pct", 25.0)),
        "target_enabled": bool(settings.get("target_enabled", False)),
        "target_profit": float(settings.get("target_profit", 3900.0)),
        "enabled": True,
    }


def load_accounts(settings_loader: Callable[[], Dict[str, Any]]) -> List[Dict[str, Any]]:
    settings = settings_loader() or {}
    with _ACCOUNTS_LOCK:
        accounts: List[Dict[str, Any]] = []
        if os.path.exists(ACCOUNTS_PATH):
            try:
                with open(ACCOUNTS_PATH, "r") as f:
                    raw = json.load(f)
                    if isinstance(raw, list):
                        accounts = raw
            except Exception:
                accounts = []

        # Ensure primary account exists & stays synced with settings.json API keys
        primary = next((a for a in accounts if a.get("id") == "primary"), None)
        if primary is None:
            primary = _default_primary_from_settings(settings)
            accounts.insert(0, primary)
        else:
            if settings.get("kite_api_key"):
                primary["api_key"] = settings["kite_api_key"]
            if settings.get("kite_api_secret"):
                primary["api_secret"] = settings["kite_api_secret"]
            primary["dry_run"] = bool(settings.get("dry_run", primary.get("dry_run", True)))
            primary["max_loss"] = float(settings.get("max_loss", primary.get("max_loss", 2500.0)))
            primary["trailing_activation"] = float(settings.get("trailing_activation", primary.get("trailing_activation", 1500.0)))
            primary["trailing_gap"] = float(settings.get("trailing_gap", primary.get("trailing_gap", 750.0)))
            primary["per_pos_sl_pct"] = float(settings.get("per_pos_sl_pct", primary.get("per_pos_sl_pct", 25.0)))
            primary["target_enabled"] = bool(settings.get("target_enabled", primary.get("target_enabled", False)))
            primary["target_profit"] = float(settings.get("target_profit", primary.get("target_profit", 3900.0)))
            primary["mtm_sl_enabled"] = bool(settings.get("mtm_sl_enabled", primary.get("mtm_sl_enabled", True)))
            primary["trailing_sl_enabled"] = bool(settings.get("trailing_sl_enabled", primary.get("trailing_sl_enabled", True)))
            primary["per_pos_sl_enabled"] = bool(settings.get("per_pos_sl_enabled", primary.get("per_pos_sl_enabled", True)))

        return accounts


def save_accounts(accounts: List[Dict[str, Any]]) -> None:
    with _ACCOUNTS_LOCK:
        with open(ACCOUNTS_PATH, "w") as f:
            json.dump(accounts, f, indent=2)


def get_account_by_id(account_id: str, settings_loader: Callable[[], Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    accounts = load_accounts(settings_loader)
    return next((a for a in accounts if a.get("id") == account_id), None)


def add_or_update_account(
    payload: Dict[str, Any],
    settings_loader: Callable[[], Dict[str, Any]],
    settings_saver: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    accounts = load_accounts(settings_loader)
    acc_id = (payload.get("id") or "").strip()

    if not acc_id:
        # Generate next acc_N id
        existing_nums = []
        for a in accounts:
            aid = str(a.get("id", ""))
            if aid.startswith("acc_"):
                try:
                    existing_nums.append(int(aid.split("_", 1)[1]))
                except ValueError:
                    pass
        next_n = max(existing_nums, default=1) + 1
        acc_id = f"acc_{next_n}"

    target = next((a for a in accounts if a.get("id") == acc_id), None)
    if target is None:
        target = {
            "id": acc_id,
            "label": (payload.get("label") or f"Account {acc_id}").strip(),
            "api_key": (payload.get("api_key") or "").strip(),
            "api_secret": (payload.get("api_secret") or "").strip(),
            "lot_multiplier": max(1, int(payload.get("lot_multiplier") or 1)),
            "dry_run": bool(payload.get("dry_run", True)),
            "mtm_sl_enabled": bool(payload.get("mtm_sl_enabled", True)),
            "max_loss": abs(float(payload.get("max_loss", 2500.0))),
            "trailing_sl_enabled": bool(payload.get("trailing_sl_enabled", True)),
            "trailing_activation": float(payload.get("trailing_activation", 1500.0)),
            "trailing_gap": float(payload.get("trailing_gap", 750.0)),
            "per_pos_sl_enabled": bool(payload.get("per_pos_sl_enabled", True)),
            "per_pos_sl_pct": float(payload.get("per_pos_sl_pct", 25.0)),
            "target_enabled": bool(payload.get("target_enabled", False)),
            "target_profit": float(payload.get("target_profit", 3900.0)),
            "enabled": bool(payload.get("enabled", True)),
        }
        accounts.append(target)
    else:
        for str_k in ("label", "api_key", "api_secret"):
            if str_k in payload and str(payload[str_k]).strip():
                target[str_k] = str(payload[str_k]).strip()
        for bool_k in ("dry_run", "mtm_sl_enabled", "trailing_sl_enabled", "per_pos_sl_enabled", "target_enabled", "enabled"):
            if bool_k in payload:
                target[bool_k] = bool(payload[bool_k])
        for num_k in ("max_loss", "trailing_activation", "trailing_gap", "per_pos_sl_pct", "target_profit"):
            if num_k in payload:
                try:
                    target[num_k] = float(payload[num_k])
                except (TypeError, ValueError):
                    pass
        if "lot_multiplier" in payload:
            try:
                target["lot_multiplier"] = max(1, int(payload["lot_multiplier"]))
            except (TypeError, ValueError):
                pass

    save_accounts(accounts)

    # Sync primary back to settings.json if primary was updated
    if acc_id == "primary" and settings_saver is not None:
        s = settings_loader() or {}
        for k in ("dry_run", "mtm_sl_enabled", "max_loss", "trailing_sl_enabled", "trailing_activation", "trailing_gap", "per_pos_sl_enabled", "per_pos_sl_pct", "target_enabled", "target_profit"):
            if k in target:
                s[k] = target[k]
        s["sl_enabled"] = target.get("mtm_sl_enabled", True)
        settings_saver(s)

    return target


def delete_account(account_id: str, settings_loader: Callable[[], Dict[str, Any]]) -> bool:
    if not account_id or account_id == "primary":
        return False
    accounts = load_accounts(settings_loader)
    new_list = [a for a in accounts if a.get("id") != account_id]
    if len(new_list) == len(accounts):
        return False
    save_accounts(new_list)
    return True


def make_account_settings_loader(account_id: str, global_settings_loader: Callable[[], Dict[str, Any]]) -> Callable[[], Dict[str, Any]]:
    """Return a settings_loader function scoped to a specific account."""
    if not account_id or account_id == "primary":
        return global_settings_loader

    def _loader() -> Dict[str, Any]:
        base = dict(global_settings_loader() or {})
        acc = get_account_by_id(account_id, global_settings_loader)
        if acc:
            base["kite_api_key"] = acc.get("api_key", "")
            base["kite_api_secret"] = acc.get("api_secret", "")
            for k in ("dry_run", "mtm_sl_enabled", "max_loss", "trailing_sl_enabled", "trailing_activation", "trailing_gap", "per_pos_sl_enabled", "per_pos_sl_pct", "target_enabled", "target_profit"):
                if k in acc:
                    base[k] = acc[k]
            base["sl_enabled"] = acc.get("mtm_sl_enabled", True)
        return base

    return _loader


def make_account_settings_saver(
    account_id: str,
    global_settings_loader: Callable[[], Dict[str, Any]],
    global_settings_saver: Callable[[Dict[str, Any]], None],
) -> Callable[[Dict[str, Any]], None]:
    """Return a settings_saver function scoped to a specific account."""
    if not account_id or account_id == "primary":
        return global_settings_saver

    def _saver(updated: Dict[str, Any]) -> None:
        updated["id"] = account_id
        add_or_update_account(updated, global_settings_loader, global_settings_saver)

    return _saver
