"""
Flask + Flask-SocketIO web dashboard for StockEdge Options — NSE Stock Futures
Analysis & Execution Engine.

Responsibilities:
  - Serves the single-page dashboard (templates/index.html)
  - Reads/writes settings.json
  - Runs futures_engine.analyze_only() or .run() in a background thread
  - Relays engine on_event/on_tick callbacks to the browser over WebSocket
  - Serves generated TRADE-ANALYSIS-*.md reports
  - Relays Stop clicks to the engine's stop_event

Run: python app.py  or  run.bat
Opens browser to http://127.0.0.1:5050 automatically.
Local-only: binds to 127.0.0.1.

DISCLAIMER: For educational and research purposes only. Not financial advice.
"""

import datetime
import http.server
import json
import os
import sys
import threading
import time
import webbrowser

# Ensure Windows stdout/stderr handles UTF-8 safely without charmap crashes
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_socketio import SocketIO

from concurrent.futures import ThreadPoolExecutor

import futures_engine
import scanner_engine as scanner_mod
import scanner_backtest
import ai_orchestrator
import position_guardian
import account_manager
from kite_client import KiteClient, extract_request_token, load_cached_access_token, set_callback_result

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
PORT = int(os.environ.get("PORT", 5050))

SERVER_STARTED_AT = datetime.datetime.now()
_WATCHED_PY_FILES = ["app.py", "futures_engine.py", "kite_client.py", "futures_signal.py",
                     "ai_orchestrator.py", "decision.py", "trade_score.py", "report_writer.py"]


def _code_last_modified():
    mtimes = []
    for fname in _WATCHED_PY_FILES:
        path = os.path.join(BASE_DIR, fname)
        if os.path.exists(path):
            mtimes.append(datetime.datetime.fromtimestamp(os.path.getmtime(path)))
    return max(mtimes) if mtimes else None


def restart_status():
    code_modified = _code_last_modified()
    needed = code_modified is not None and code_modified > SERVER_STARTED_AT
    return {
        "restart_needed":    needed,
        "server_started_at": SERVER_STARTED_AT.strftime("%H:%M:%S"),
        "code_last_modified": code_modified.strftime("%H:%M:%S") if code_modified else None,
    }


app = Flask(__name__)
app.config["SECRET_KEY"] = os.urandom(24).hex()
app.config["TEMPLATES_AUTO_RELOAD"] = True
socketio = SocketIO(app, async_mode="threading")

STATE_LOCK = threading.Lock()
STATE = {
    "kite_client":              None,
    "logged_in":                False,
    "running":                  False,
    "stop_event":               None,
    "last_analysis":            None,   # last full analysis result dict
    "last_universe_event":      None,
    "last_option_chain":        None,
    "scanner":                  None,   # ScannerEngine instance
    "scanner_running":          False,
    "guardian":                 None,   # Primary PositionGuardian instance
    "accounts":                 {},     # account_id -> {client, logged_in, user_name, guardian}
    "pending_login_account_id": "primary",
}


def _ensure_guardian_started(client, account_id="primary"):
    """Ensure the PositionGuardian is running for the given logged-in Kite client."""
    if client is None:
        return None
    acc_id = account_id or "primary"
    acc_loader = account_manager.make_account_settings_loader(acc_id, load_settings)
    acc_saver = account_manager.make_account_settings_saver(acc_id, load_settings, save_settings)

    with STATE_LOCK:
        acc_entry = STATE["accounts"].setdefault(acc_id, {
            "client": client,
            "logged_in": True,
            "user_name": getattr(client, "_user_name", "Kite User"),
            "guardian": None,
        })
        acc_entry["client"] = client
        acc_entry["logged_in"] = True
        acc_entry["user_name"] = getattr(client, "_user_name", acc_entry.get("user_name", "Kite User"))

        g = acc_entry.get("guardian")
        if g is None:
            g = position_guardian.PositionGuardian(
                kite_client=client,
                settings_loader=acc_loader,
                settings_saver=acc_saver,
                on_update=lambda st, aid=acc_id: socketio.emit("guardian_update", {**st, "account_id": aid}),
                on_alert=lambda al, aid=acc_id: socketio.emit("guardian_alert", {**al, "account_id": aid}),
                log=_make_log(),
            )
            acc_entry["guardian"] = g
        else:
            g.kite_client = client

        if acc_id == "primary":
            STATE["guardian"] = g
    if not g.is_running():
        g.start()
    return g


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def load_settings():
    settings = {}
    example_path = os.path.join(BASE_DIR, "settings.example.json")
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r") as f:
                settings = json.load(f)
        except Exception:
            settings = {}
    elif os.path.exists(example_path):
        try:
            with open(example_path, "r") as f:
                settings = json.load(f)
        except Exception:
            settings = {}

    # Override or populate via Environment Variables (for Render / Docker / Cloud)
    env_keys = {
        "KITE_API_KEY": "kite_api_key",
        "KITE_API_SECRET": "kite_api_secret",
        "GEMINI_API_KEY": "gemini_api_key",
        "GEMINI_MODEL": "gemini_model",
        "ANTHROPIC_API_KEY": "anthropic_api_key",
        "CLAUDE_MODEL": "claude_model",
        "AI_PROVIDER": "ai_provider",
    }
    for env_var, key in env_keys.items():
        v = os.environ.get(env_var)
        if v:
            settings[key] = v.strip()

    if os.environ.get("DRY_RUN") is not None:
        settings["dry_run"] = os.environ.get("DRY_RUN", "").lower() in ("true", "1", "yes")

    return settings


def save_settings(settings):
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)


def settings_configured(settings):
    if not settings.get("kite_api_key") or not settings.get("kite_api_secret"):
        return False
    provider = settings.get("ai_provider", "gemini")
    if provider == "gemini" and not settings.get("gemini_api_key"):
        return False
    if provider == "claude" and not settings.get("anthropic_api_key"):
        return False
    return True


# ---------------------------------------------------------------------------
# Log / event helpers
# ---------------------------------------------------------------------------

def _make_log():
    def log(message):
        try:
            print(message, flush=True)
        except Exception:
            try:
                print(str(message).encode("ascii", errors="replace").decode("ascii"))
            except Exception:
                pass
        try:
            socketio.emit("log", {"message": str(message)})
        except Exception:
            pass
    return log


def _make_event_relays():
    def on_event(evt):
        evt_type = evt.get("type")
        if evt_type in ("universe_ready", "decision", "analysis_complete", "option_chain"):
            with STATE_LOCK:
                if evt_type == "universe_ready":
                    STATE["last_universe_event"] = evt
                elif evt_type in ("decision", "analysis_complete"):
                    STATE["last_analysis"] = evt
                    if evt.get("option_chain"):
                        STATE["last_option_chain"] = evt.get("option_chain")
                elif evt_type == "option_chain":
                    STATE["last_option_chain"] = evt.get("option_chain")
        socketio.emit("engine_event", evt)

    def on_tick(tick):
        socketio.emit("tick", {
            "instrument_token": tick.get("instrument_token"),
            "last_price":       tick.get("last_price"),
            "average_price":    tick.get("average_price"),
        })

    return on_event, on_tick


def _spawn_engine_thread(runnable, log):
    def _run():
        try:
            runnable()
        except InterruptedError:
            log("[app] engine stopped by user")
            socketio.emit("engine_event", {"type": "stopped", "message": "Analysis stopped by user."})
        except Exception as exc:  # noqa: BLE001
            log(f"[app] engine run failed: {exc!r}")
            socketio.emit("engine_event", {"type": "error", "message": str(exc)})
        finally:
            with STATE_LOCK:
                STATE["running"] = False
                STATE["stop_event"] = None
            socketio.emit("engine_status", {"running": False})

    socketio.emit("engine_status", {"running": True})
    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Routes — pages
# ---------------------------------------------------------------------------

def _exchange_and_set_token(token, account_id=None):
    with STATE_LOCK:
        acc_id = account_id or STATE.get("pending_login_account_id") or "primary"
    acc_settings = account_manager.make_account_settings_loader(acc_id, load_settings)()
    log = _make_log()
    client = KiteClient(acc_settings, log=log, account_id=acc_id)
    client.complete_login(token)
    try:
        prof = client.kite.profile()
        client._user_name = prof.get("user_name") or prof.get("user_id") or "Kite User"
    except Exception:
        client._user_name = "Kite User"
    with STATE_LOCK:
        if acc_id == "primary":
            STATE["kite_client"] = client
            STATE["logged_in"] = True
        STATE["pending_login_account_id"] = "primary"
    _ensure_guardian_started(client, account_id=acc_id)
    set_callback_result(token=token, error=None)
    socketio.emit("login_status", {"status": "success", "source": "redirect", "account_id": acc_id, "user_name": client._user_name})
    log(f"[auth] successfully exchanged request_token for [{acc_id}]! (user: {client._user_name})")
    return client


class Port5000BridgeHandler(http.server.BaseHTTPRequestHandler):
    """Listens on port 5000 to catch Zerodha Kite OAuth redirects if the developer
    console is configured with http://127.0.0.1:5000/ or http://localhost:5000/.
    Captures the request_token, exchanges it, and forwards the browser to port 5050.
    """
    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        token = query.get("request_token", [None])[0]
        if token:
            print(f"[port-5000-bridge] received request_token={token[:8]}... from Kite redirect!")
            try:
                _exchange_and_set_token(token)
            except Exception as exc:
                print(f"[port-5000-bridge] token exchange failed: {exc}")

        target = f"http://127.0.0.1:{PORT}{parsed.path}"
        if parsed.query:
            target += f"?{parsed.query}"

        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        html = (
            f"<!doctype html><html><head><meta http-equiv='refresh' content='0;url={target}'></head>"
            f"<body style='font-family:sans-serif;padding:30px;background:#0d1117;color:#e2e8f0;text-align:center;'>"
            f"<h2>Connecting to TradeAI on port {PORT}...</h2>"
            f"<p><a href='{target}' style='color:#38bdf8;'>Click here if not redirected automatically</a></p>"
            f"<script>window.location.href='{target}';</script></body></html>"
        )
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        pass


def _start_port_5000_bridge():
    if os.environ.get("RENDER") or PORT == 5000:
        return
    try:
        bridge_server = http.server.ThreadingHTTPServer(("0.0.0.0", 5000), Port5000BridgeHandler)
        t = threading.Thread(target=bridge_server.serve_forever, daemon=True)
        t.start()
        print(f"[app] Port 5000 redirect bridge active (forwards Zerodha Kite redirects to port {PORT})")
    except Exception as exc:
        print(f"[app] Note: Port 5000 bridge not started ({exc}) — direct port {PORT} callback remains active")


def _handle_kite_redirect():
    """Handles Kite login redirect on either /kite/callback, /callback, or / (dashboard root).
    Exchanges request_token for an access_token, writes token.json, updates state,
    and returns an appropriate HTML response or redirect.
    """
    from urllib.parse import parse_qs, urlparse
    from flask import Response, redirect

    query = parse_qs(urlparse(request.url).query)
    token = query.get("request_token", [None])[0]
    status = query.get("status", [None])[0]
    action = query.get("action", [None])[0]
    dashboard = f"{request.scheme}://{request.host}/"
    log = _make_log()

    log(f"[auth] received redirect on '{request.path}': token={'yes' if token else 'no'}, status={status}, action={action}")

    if token:
        try:
            client = _exchange_and_set_token(token)
            user_name = getattr(client, "_user_name", "Kite User")
        except Exception as exc:
            log(f"[auth] token exchange failed: {exc!r}")
            set_callback_result(token=None, error=str(exc))
            socketio.emit("login_status", {"status": "error", "message": str(exc), "source": "callback"})
            with STATE_LOCK:
                if STATE.get("logged_in") and STATE.get("kite_client") is not None:
                    return redirect("/")
            body = (
                f"<!doctype html><html><head><meta charset='utf-8'><title>Kite Login Failed - TradeAI</title>"
                f"<style>body{{font-family:Segoe UI,sans-serif;padding:40px;background:#0d1117;color:#e2e8f0;display:flex;align-items:center;justify-content:center;height:80vh}}"
                f".box{{background:#161b22;border:1px solid #ef4444;border-radius:12px;padding:32px;max-width:540px;width:100%;text-align:center}}"
                f"h2{{color:#ef4444;margin-top:0}}.err{{background:#450a0a;color:#fca5a5;padding:12px;border-radius:8px;font-family:monospace;margin:16px 0;word-break:break-all}}"
                f"a{{display:inline-block;margin-top:16px;padding:10px 20px;background:#2563eb;color:#fff;text-decoration:none;border-radius:6px;font-weight:600}}"
                f"</style></head><body><div class='box'>"
                f"<h2>&#10007; Kite Login Failed</h2>"
                f"<p>The Kite request token could not be exchanged for an access token:</p>"
                f"<div class='err'>{exc}</div>"
                f"<p><a href='{dashboard}'>&larr; Back to TradeAI Dashboard</a></p>"
                f"</div></body></html>"
            )
            return Response(body, status=400, mimetype="text/html")

        if request.path in ("/", ""):
            return redirect("/")

        body = (
            f"<!doctype html><html><head><meta charset='utf-8'><title>Kite Login Successful - TradeAI</title>"
            f'<meta http-equiv="refresh" content="1;url={dashboard}">'
            f'<script>setTimeout(function(){{location.href="{dashboard}";}},1000);</script>'
            f"<style>body{{font-family:Segoe UI,sans-serif;padding:40px;background:#0d1117;color:#e2e8f0;display:flex;align-items:center;justify-content:center;height:80vh}}"
            f".box{{background:#161b22;border:1px solid #22c55e;border-radius:12px;padding:32px;max-width:540px;width:100%;text-align:center}}"
            f"h2{{color:#22c55e;margin-top:0}}p{{color:#94a3b8;font-size:15px}}"
            f"a{{display:inline-block;margin-top:16px;padding:10px 20px;background:#2563eb;color:#fff;text-decoration:none;border-radius:6px;font-weight:600}}"
            f"</style></head><body><div class='box'>"
            f"<h2>&#10003; Kite Login Successful!</h2>"
            f"<p>Connected as <strong>{user_name}</strong>. Redirecting you back to TradeAI Dashboard in 1 second...</p>"
            f"<p><a href='{dashboard}'>Click here if not redirected automatically</a></p>"
            f"</div></body></html>"
        )
        return Response(body, status=200, mimetype="text/html")
    else:
        reason = (query.get("message", [None])[0] or status or "no request_token in redirect")
        set_callback_result(token=None, error=reason)
        log(f"[auth] redirect error received: {reason}")
        socketio.emit("login_status", {"status": "error", "message": reason, "source": "callback"})
        body = (
            f"<!doctype html><html><head><meta charset='utf-8'><title>Kite Login Failed - TradeAI</title>"
            f"<style>body{{font-family:Segoe UI,sans-serif;padding:40px;background:#0d1117;color:#e2e8f0;display:flex;align-items:center;justify-content:center;height:80vh}}"
            f".box{{background:#161b22;border:1px solid #ef4444;border-radius:12px;padding:32px;max-width:540px;width:100%;text-align:center}}"
            f"h2{{color:#ef4444;margin-top:0}}.err{{background:#450a0a;color:#fca5a5;padding:12px;border-radius:8px;font-family:monospace;margin:16px 0;word-break:break-all}}"
            f"a{{display:inline-block;margin-top:16px;padding:10px 20px;background:#2563eb;color:#fff;text-decoration:none;border-radius:6px;font-weight:600}}"
            f"</style></head><body><div class='box'>"
            f"<h2>&#10007; Kite Login Failed</h2>"
            f"<p>Kite returned an error or missing token:</p>"
            f"<div class='err'>{reason}</div>"
            f"<p><a href='{dashboard}'>&larr; Back to TradeAI Dashboard</a></p>"
            f"</div></body></html>"
        )
        return Response(body, status=400, mimetype="text/html")


@app.route("/")
def index():
    if request.args.get("request_token") or request.args.get("status") in ("success", "error"):
        resp = _handle_kite_redirect()
        if resp is not None:
            return resp

    settings = load_settings()
    user_name = None
    with STATE_LOCK:
        logged_in = STATE.get("logged_in", False)
        client = STATE.get("kite_client")
        if client and logged_in:
            user_name = getattr(client, "_user_name", None)
    return render_template(
        "index.html",
        settings=settings,
        configured=settings_configured(settings),
        restart=restart_status(),
        logged_in=logged_in,
        user_name=user_name,
    )


# ---------------------------------------------------------------------------
# Routes — settings
# ---------------------------------------------------------------------------

@app.route("/api/settings", methods=["GET"])
def get_settings():
    settings = load_settings()
    return jsonify({"settings": settings, "configured": settings_configured(settings)})


@app.route("/api/settings", methods=["POST"])
def update_settings():
    incoming = request.get_json(force=True, silent=True) or {}
    settings = load_settings()

    str_fields = ["kite_api_key", "kite_api_secret", "ai_provider", "gemini_api_key",
                  "gemini_model", "anthropic_api_key", "claude_model",
                  "ticker", "exchange", "time_exit", "custom_ai_note", "reports_dir"]
    for field in str_fields:
        if field in incoming:
            settings[field] = str(incoming[field]).strip()

    if "lots" in incoming:
        try:
            settings["lots"] = max(1, int(incoming["lots"]))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "lots must be a whole number"}), 400

    for bool_field in ["sl_enabled", "target_enabled", "time_exit_enabled"]:
        if bool_field in incoming:
            settings[bool_field] = bool(incoming[bool_field])

    for num_field in ["max_loss", "target_profit", "buy_score_threshold", "sell_score_threshold"]:
        if num_field in incoming:
            try:
                settings[num_field] = float(incoming[num_field])
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": f"{num_field} must be a number"}), 400

    if isinstance(incoming.get("proxy"), dict):
        settings.setdefault("proxy", {})
        for k in ["enabled", "host", "port", "user", "pass"]:
            if k in incoming["proxy"]:
                settings["proxy"][k] = incoming["proxy"][k]

    save_settings(settings)
    return jsonify({"ok": True, "configured": settings_configured(settings)})


@app.route("/api/mode", methods=["POST"])
def set_mode():
    with STATE_LOCK:
        if STATE["running"]:
            return jsonify({"ok": False, "error": "cannot change mode while engine is running — Stop first"}), 400
    incoming = request.get_json(force=True, silent=True) or {}
    if "live" not in incoming:
        return jsonify({"ok": False, "error": "missing 'live' field"}), 400
    settings = load_settings()
    settings["dry_run"] = not bool(incoming["live"])
    save_settings(settings)
    with STATE_LOCK:
        if STATE.get("kite_client"):
            STATE["kite_client"].settings["dry_run"] = settings["dry_run"]
    log = _make_log()
    log(f"[app] mode switched to {'LIVE — real orders will be placed' if incoming['live'] else 'DRY RUN'}")
    return jsonify({"ok": True, "dry_run": settings["dry_run"]})


# ---------------------------------------------------------------------------
# Routes — auth
# ---------------------------------------------------------------------------

@app.route("/kite/callback", methods=["GET"])
@app.route("/callback", methods=["GET"])
@app.route("/login/callback", methods=["GET"])
def kite_callback():
    """Kite redirects here after the user logs in when Redirect URL is set to:
    http://127.0.0.1:5050/kite/callback (or /callback)
    """
    return _handle_kite_redirect()

@app.route("/api/login", methods=["POST"])
def login():
    incoming = request.get_json(force=True, silent=True) or {}
    acc_id = (incoming.get("account_id") or "primary").strip()
    force = incoming.get("force", False)

    with STATE_LOCK:
        if acc_id == "primary" and STATE["running"]:
            return jsonify({"ok": False, "error": "engine is running — stop it first"}), 400
        STATE["pending_login_account_id"] = acc_id

    acc_settings = account_manager.make_account_settings_loader(acc_id, load_settings)()
    if not acc_settings.get("kite_api_key") or not acc_settings.get("kite_api_secret"):
        return jsonify({"ok": False, "error": f"Kite API key/secret not set for {acc_id}"}), 400

    log = _make_log()
    client_pre = KiteClient(acc_settings, log=log, account_id=acc_id)
    login_url = client_pre.kite.login_url()

    with STATE_LOCK:
        acc_entry = STATE["accounts"].get(acc_id, {})
        client = acc_entry.get("client") or (STATE.get("kite_client") if acc_id == "primary" else None)
        is_logged = acc_entry.get("logged_in") or (STATE.get("logged_in") if acc_id == "primary" else False)
        if not force and client and is_logged:
            try:
                prof = client.kite.profile()
                user_name = prof.get("user_name") or prof.get("user_id") or "Kite User"
                client._user_name = user_name
                return jsonify({
                    "ok": True,
                    "already_logged_in": True,
                    "account_id": acc_id,
                    "login_url": login_url,
                    "user_name": user_name,
                    "message": f"Already connected to Kite [{acc_id}] as {user_name}"
                })
            except Exception:
                if acc_id == "primary":
                    STATE["logged_in"] = False
                    STATE["kite_client"] = None
                if acc_id in STATE["accounts"]:
                    STATE["accounts"][acc_id]["logged_in"] = False

    set_callback_result(token=None, error=None)
    socketio.emit("login_status", {"status": "waiting", "source": "redirect", "account_id": acc_id})

    return jsonify({
        "ok": True,
        "account_id": acc_id,
        "login_url": login_url,
        "message": f"Redirecting to Kite for [{acc_id}] authentication..."
    })


@app.route("/api/login/manual", methods=["POST"])
def login_manual():
    incoming = request.get_json(force=True, silent=True) or {}
    acc_id = (incoming.get("account_id") or "primary").strip()

    with STATE_LOCK:
        if acc_id == "primary" and STATE["running"]:
            return jsonify({"ok": False, "error": "engine is running — stop it first"}), 400

    acc_settings = account_manager.make_account_settings_loader(acc_id, load_settings)()
    if not acc_settings.get("kite_api_key") or not acc_settings.get("kite_api_secret"):
        return jsonify({"ok": False, "error": f"Kite API key/secret not set for {acc_id}"}), 400

    raw_input = str(incoming.get("token_input", ""))
    try:
        request_token = extract_request_token(raw_input)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    log = _make_log()
    try:
        client = KiteClient(acc_settings, log=log, account_id=acc_id)
        try:
            client.complete_login(request_token)
        except Exception as exc_req:
            # Fallback: if user pasted a live access_token directly instead of request_token
            try:
                client.kite.set_access_token(request_token)
                client.kite.profile()
                client._save_token(request_token)
            except Exception:
                raise exc_req
        try:
            prof = client.kite.profile()
            client._user_name = prof.get("user_name") or prof.get("user_id") or "Kite User"
        except Exception:
            client._user_name = "Kite User"
        with STATE_LOCK:
            if acc_id == "primary":
                STATE["kite_client"] = client
                STATE["logged_in"] = True
        _ensure_guardian_started(client, account_id=acc_id)
        set_callback_result(token=request_token, error=None)
        socketio.emit("login_status", {"status": "success", "source": "manual", "account_id": acc_id, "user_name": client._user_name})
        log(f"[app] manual login successful for [{acc_id}] {client._user_name} (token {request_token[:8]}...)")
        return jsonify({"ok": True, "account_id": acc_id, "user_name": client._user_name, "message": f"Connected [{acc_id}] as {client._user_name}!"})
    except Exception as exc:  # noqa: BLE001
        print(f"[app] manual login failed for [{acc_id}]: {exc!r}")
        log(f"[app] manual login failed for [{acc_id}]: {exc!r}")
        socketio.emit("login_status", {"status": "error", "message": str(exc), "source": "manual", "account_id": acc_id})
        return jsonify({"ok": False, "error": f"{exc} (Check API Key/Secret for {acc_id} & ensure token is fresh/unused)"}), 400


# ---------------------------------------------------------------------------
# Routes — engine control
# ---------------------------------------------------------------------------

@app.route("/api/analyze", methods=["POST"])
def analyze():
    """
    Runs the full 3-phase AI analysis and writes the report.
    NEVER places any order — 'analyze first, you decide.'
    """
    incoming = request.get_json(force=True, silent=True) or {}
    ticker_override = str(incoming.get("ticker", "")).strip().upper() or None

    with STATE_LOCK:
        if not STATE["logged_in"] or STATE["kite_client"] is None:
            return jsonify({"ok": False, "error": "please Login first"}), 400
        if STATE["running"]:
            return jsonify({"ok": False, "error": "engine is already running"}), 400
        STATE["running"] = True
        stop_event = threading.Event()
        STATE["stop_event"] = stop_event
        kite_client = STATE["kite_client"]

    settings = load_settings()
    active_ticker = ticker_override or settings.get("ticker", "RELIANCE")
    if ticker_override:
        settings["ticker"] = ticker_override
        save_settings(settings)

    log = _make_log()
    on_event, on_tick = _make_event_relays()

    _spawn_engine_thread(
        lambda: futures_engine.analyze_only(
            stop_event=stop_event,
            settings_path=SETTINGS_PATH, log=log,
            kite_client=kite_client, on_event=on_event, on_tick=on_tick,
            ticker_override=active_ticker, settings_dict=settings,
        ),
        log,
    )
    return jsonify({"ok": True, "ticker": active_ticker})


@app.route("/api/execute", methods=["POST"])
def execute():
    """
    Runs analysis then enters a futures position if the decision is LONG/SHORT.
    The user must explicitly call this after reviewing the analysis.
    """
    incoming = request.get_json(force=True, silent=True) or {}
    ticker_override = str(incoming.get("ticker", "")).strip().upper() or None

    with STATE_LOCK:
        if not STATE["logged_in"] or STATE["kite_client"] is None:
            return jsonify({"ok": False, "error": "please Login first"}), 400
        if STATE["running"]:
            return jsonify({"ok": False, "error": "engine is already running"}), 400
        STATE["running"] = True
        stop_event = threading.Event()
        STATE["stop_event"] = stop_event
        kite_client = STATE["kite_client"]

    settings = load_settings()
    active_ticker = ticker_override or settings.get("ticker", "RELIANCE")
    if ticker_override:
        settings["ticker"] = ticker_override
        save_settings(settings)

    log = _make_log()
    on_event, on_tick = _make_event_relays()

    _spawn_engine_thread(
        lambda: futures_engine.run(
            stop_event=stop_event, settings_path=SETTINGS_PATH, log=log,
            kite_client=kite_client, on_event=on_event, on_tick=on_tick,
            ticker_override=active_ticker, settings_dict=settings,
        ),
        log,
    )
    return jsonify({"ok": True, "ticker": active_ticker})


@app.route("/api/manual_trade", methods=["POST"])
def manual_trade():
    """Manual direction override — enters LONG or SHORT without analysis."""
    incoming = request.get_json(force=True, silent=True) or {}
    direction = str(incoming.get("direction", "")).upper()
    if direction not in futures_engine.VALID_MANUAL_DIRECTIONS:
        return jsonify({"ok": False, "error": "direction must be LONG or SHORT"}), 400

    with STATE_LOCK:
        if not STATE["logged_in"] or STATE["kite_client"] is None:
            return jsonify({"ok": False, "error": "please Login first"}), 400
        if STATE["running"]:
            return jsonify({"ok": False, "error": "engine is already running"}), 400
        STATE["running"] = True
        stop_event = threading.Event()
        STATE["stop_event"] = stop_event
        kite_client = STATE["kite_client"]

    log = _make_log()
    on_event, on_tick = _make_event_relays()

    _spawn_engine_thread(
        lambda: futures_engine.manual_run(
            direction, stop_event=stop_event, settings_path=SETTINGS_PATH, log=log,
            kite_client=kite_client, on_event=on_event, on_tick=on_tick,
        ),
        log,
    )
    return jsonify({"ok": True, "direction": direction})


@app.route("/api/stop", methods=["POST"])
def stop():
    with STATE_LOCK:
        stop_event = STATE.get("stop_event")
        running = STATE.get("running", False)
    if not running:
        return jsonify({"ok": False, "error": "engine is not running"}), 400
    if stop_event is not None:
        stop_event.set()
    return jsonify({"ok": True, "message": "Stop signal sent - halting execution/analysis."})


@app.route("/api/status", methods=["GET"])
def status():
    with STATE_LOCK:
        logged_in = STATE["logged_in"]
        running = STATE["running"]
        client = STATE.get("kite_client")
        user_name = getattr(client, "_user_name", None) if client else None
    result = {
        "logged_in": logged_in,
        "running": running,
        "user_name": user_name or ("Kite User" if logged_in else None),
    }
    result.update(restart_status())
    return jsonify(result)


@app.route("/api/option_chain", methods=["GET"])
def option_chain_endpoint():
    """
    Returns live 11-strike option chain data (ATM +/- 5 strikes) for the requested ticker.
    Optional query parameter: ?ticker=... (e.g. /api/option_chain?ticker=INFY)
    """
    settings = load_settings()
    req_ticker = (request.args.get("ticker") or "").strip().upper()
    ticker = req_ticker or settings.get("ticker", "RELIANCE")

    with STATE_LOCK:
        kite_client = STATE["kite_client"]
        last_opt = STATE.get("last_option_chain")
        if last_opt and last_opt.get("ticker") == ticker and not request.args.get("force"):
            return jsonify({"ok": True, "option_chain": last_opt})

    if not kite_client:
        return jsonify({"ok": False, "error": "not logged in — login to Kite first"}), 400

    try:
        chain_data = futures_engine.build_option_chain_data(
            kite_client=kite_client,
            ticker=ticker,
            settings=settings,
            log=_make_log(),
        )
        if not chain_data:
            return jsonify({"ok": False, "error": f"option chain unavailable for {ticker}"}), 404
        with STATE_LOCK:
            STATE["last_option_chain"] = chain_data
        return jsonify({"ok": True, "option_chain": chain_data})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/last_analysis", methods=["GET"])
def last_analysis():
    with STATE_LOCK:
        analysis_data = STATE["last_analysis"]
        opt_data = STATE.get("last_option_chain")
        if analysis_data and not opt_data and analysis_data.get("option_chain"):
            opt_data = analysis_data.get("option_chain")
        return jsonify({
            "universe":      STATE["last_universe_event"],
            "analysis":      analysis_data,
            "option_chain":  opt_data,
        })


# ---------------------------------------------------------------------------
# Routes — reports
# ---------------------------------------------------------------------------

@app.route("/api/reports", methods=["GET"])
def list_reports():
    """List all generated TRADE-ANALYSIS-*.md report files."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    files = sorted(
        [f for f in os.listdir(REPORTS_DIR) if f.startswith("TRADE-ANALYSIS-") and f.endswith(".md")],
        reverse=True,
    )
    report_list = []
    for fname in files:
        fpath = os.path.join(REPORTS_DIR, fname)
        stat = os.stat(fpath)
        ticker = fname.replace("TRADE-ANALYSIS-", "").replace(".md", "")
        report_list.append({
            "filename":  fname,
            "ticker":    ticker,
            "size":      stat.st_size,
            "modified":  datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return jsonify({"ok": True, "reports": report_list})


@app.route("/api/reports/<path:filename>", methods=["GET"])
def get_report(filename):
    """Serve a specific report markdown file."""
    return send_from_directory(REPORTS_DIR, filename, mimetype="text/plain; charset=utf-8")


# ---------------------------------------------------------------------------
# Routes — selectable futures symbols & live market data
# ---------------------------------------------------------------------------

@app.route("/api/futures_symbols", methods=["GET"])
def futures_symbols():
    """
    Returns list of searchable/selectable NSE F&O stock futures.
    Optional query parameter: ?q=... (e.g. /api/futures_symbols?q=RELI)
    """
    query = request.args.get("q", "")
    with STATE_LOCK:
        kite_client = STATE["kite_client"]
    symbols = futures_engine.get_active_futures_symbols(kite_client=kite_client, query=query)
    return jsonify({"ok": True, "count": len(symbols), "symbols": symbols})


_LTP_CACHE = {}  # quote_key -> (timestamp, dict)

@app.route("/api/spot_ltp", methods=["GET"])
def spot_ltp():
    """Live spot LTP for the configured or requested ticker — polled while idle."""
    with STATE_LOCK:
        if not STATE["logged_in"] or STATE["kite_client"] is None:
            return jsonify({"ok": False, "error": "not logged in"}), 400
        kite_client = STATE["kite_client"]
    settings = load_settings()
    req_ticker = (request.args.get("ticker") or "").strip().upper()
    ticker = req_ticker or settings.get("ticker", "RELIANCE")
    spot_symbol = futures_engine.resolve_spot_tradingsymbol(ticker)
    quote_key = f"NSE:{spot_symbol}"
    now_ts = time.time()

    # If recent cache (< 1.5s), return it immediately to avoid Kite rate limits/timeouts
    cached = _LTP_CACHE.get(quote_key)
    if cached and (now_ts - cached[0]) < 1.5:
        return jsonify(cached[1])

    try:
        q = kite_client.get_quote([quote_key])
        qd = q[quote_key]
        ltp = qd["last_price"]
        net_change = qd.get("net_change", 0)
        prev_close = (qd.get("ohlc") or {}).get("close") or None
        if prev_close and prev_close > 0:
            change_pct = ((ltp - prev_close) / prev_close) * 100
            change_abs = ltp - prev_close
        elif net_change is not None and ltp > 0:
            prev_calc = ltp - net_change
            change_pct = (net_change / prev_calc * 100) if prev_calc > 0 else 0
            change_abs = net_change
        else:
            change_pct = 0
            change_abs = 0
        resp_data = {
            "ok": True,
            "ticker": ticker.upper(),
            "spot_symbol": spot_symbol,
            "ltp": ltp,
            "change_pct": round(change_pct, 2),
            "change_abs": round(change_abs, 2),
            "prev_close": prev_close,
        }
        _LTP_CACHE[quote_key] = (now_ts, resp_data)
        return jsonify(resp_data)
    except Exception as exc:  # noqa: BLE001
        # Fallback to last known good cache if available (up to 60s)
        if cached and (now_ts - cached[0]) < 60:
            fallback = dict(cached[1])
            fallback["cached"] = True
            return jsonify(fallback)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/spot_chart", methods=["GET"])
def spot_chart():
    """Today's 5-minute candles for the configured or requested ticker."""
    with STATE_LOCK:
        if not STATE["logged_in"] or STATE["kite_client"] is None:
            return jsonify({"ok": False, "error": "not logged in"}), 400
        kite_client = STATE["kite_client"]
    settings = load_settings()
    req_ticker = (request.args.get("ticker") or "").strip().upper()
    ticker = req_ticker or settings.get("ticker", "RELIANCE")
    try:
        spot_instr = futures_engine.get_spot_instrument(kite_client, ticker)
        spot_token = spot_instr["instrument_token"]
        spot_symbol = spot_instr["tradingsymbol"]
        today = datetime.date.today()
        market_open = datetime.datetime.combine(today, datetime.time(9, 15))
        now = datetime.datetime.now()
        candles = []
        if now > market_open:
            raw = kite_client.get_intraday_history(spot_token, market_open, now, interval="5minute")
            candles = [
                {"time": int(c["date"].timestamp()), "open": c["open"],
                 "high": c["high"], "low": c["low"], "close": c["close"]}
                for c in raw
            ]
        return jsonify({"ok": True, "ticker": ticker.upper(), "spot_symbol": spot_symbol, "candles": candles})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/prompt_preview", methods=["GET"])
def prompt_preview():
    settings = load_settings()
    ticker = settings.get("ticker", "TICKER")
    return jsonify({"prompt": ai_orchestrator.build_prompt_preview(ticker=ticker)})


# ---------------------------------------------------------------------------
# Routes — dual scanner (Strategy A: Bullish, Strategy B: Bearish)
# ---------------------------------------------------------------------------

@app.route("/api/scanner/start", methods=["POST"])
def scanner_start():
    """Start or update the background dual scanner."""
    with STATE_LOCK:
        if not STATE["logged_in"] or STATE["kite_client"] is None:
            return jsonify({"ok": False, "error": "please Login first"}), 400
        kite_client = STATE["kite_client"]
        existing_eng = STATE.get("scanner")
        was_running = STATE.get("scanner_running", False)

    settings = load_settings()

    # Parse optional body overrides for symbols / strategy toggles
    incoming = request.get_json(force=True, silent=True) or {}
    if "all_fno" in incoming and incoming["all_fno"]:
        # Signal scanner to load full FNO universe dynamically each cycle
        settings["scanner_symbols"] = ["ALL_FNO"]
    elif "symbols" in incoming:
        syms = incoming["symbols"]
        settings["scanner_symbols"] = syms if syms else ["ALL_FNO"]
    if "enabled_a" in incoming:
        settings["scanner_enabled_a"] = bool(incoming["enabled_a"])
    if "enabled_b" in incoming:
        settings["scanner_enabled_b"] = bool(incoming["enabled_b"])
    if "enabled_c" in incoming:
        settings["scanner_enabled_c"] = bool(incoming["enabled_c"])
    if "enabled_d" in incoming:
        settings["scanner_enabled_d"] = bool(incoming["enabled_d"])
    if "enabled_e" in incoming:
        settings["scanner_enabled_e"] = bool(incoming["enabled_e"])
    if "enabled_f" in incoming:
        settings["scanner_enabled_f"] = bool(incoming["enabled_f"])
    if "enabled_g" in incoming:
        settings["scanner_enabled_g"] = bool(incoming["enabled_g"])
    if "max_lot_size" in incoming:
        try:
            settings["scanner_max_lot_size"] = int(incoming["max_lot_size"])
        except (ValueError, TypeError):
            pass

    log = _make_log()

    # If already running, gracefully stop previous instance and restart with fresh settings
    if existing_eng and was_running:
        try:
            existing_eng.stop()
        except Exception:
            pass

    def _on_signal(sig):
        socketio.emit("scanner_signal", sig)

    def _on_signal_reversed(data):
        socketio.emit("scanner_signal_reversed", data)

    eng = scanner_mod.ScannerEngine(
        kite_client=kite_client,
        settings=settings,
        on_signal=_on_signal,
        on_signal_reversed=_on_signal_reversed,
        log=log,
    )
    eng.start()

    with STATE_LOCK:
        STATE["scanner"] = eng
        STATE["scanner_running"] = True

    all_fno_mode = settings["scanner_symbols"] == ["ALL_FNO"]
    socketio.emit("scanner_status", {"running": True, "all_fno": all_fno_mode})
    return jsonify({"ok": True, "message": "Scanner started / updated", "all_fno": all_fno_mode})


@app.route("/api/scanner/stop", methods=["POST"])
def scanner_stop():
    """Stop the background scanner."""
    with STATE_LOCK:
        eng = STATE.get("scanner")
        if not eng or not STATE["scanner_running"]:
            return jsonify({"ok": False, "error": "scanner is not running"}), 400

    eng.stop()
    with STATE_LOCK:
        STATE["scanner_running"] = False
        STATE["scanner"] = None

    socketio.emit("scanner_status", {"running": False})
    return jsonify({"ok": True, "message": "Scanner stopped"})


@app.route("/api/scanner/status", methods=["GET"])
def scanner_status():
    with STATE_LOCK:
        running = STATE.get("scanner_running", False)
        eng = STATE.get("scanner")
        signal_count = len(eng.get_signals()) if eng else 0
        sym_count = eng.get_symbol_count() if eng else 0
        all_fno = eng is not None and (eng.settings.get("scanner_symbols") == ["ALL_FNO"] or not eng.settings.get("scanner_symbols"))
    return jsonify({"ok": True, "running": running, "signal_count": signal_count,
                    "symbol_count": sym_count, "all_fno": all_fno})


@app.route("/api/scanner/signals", methods=["GET"])
def scanner_signals():
    with STATE_LOCK:
        eng = STATE.get("scanner")
        kite_client = STATE.get("kite_client")
    signals = eng.get_signals() if eng else []
    if kite_client and signals:
        for sig in signals:
            if not sig.get("atm_option") or not sig.get("futures_symbol"):
                try:
                    opt_type = "CE" if sig.get("direction") == "BULLISH" else "PE"
                    atm = scanner_mod.resolve_atm_option(
                        kite_client, sig["symbol"], opt_type, spot_price=sig.get("spot_price"), log=print
                    )
                    if atm:
                        if not sig.get("atm_option"):
                            sig["atm_option"] = atm
                        if not sig.get("futures_symbol") and atm.get("futures_symbol"):
                            sig["futures_symbol"] = atm["futures_symbol"]
                        if not sig.get("expiry") and atm.get("expiry"):
                            sig["expiry"] = atm["expiry"]
                except Exception:
                    pass
    return jsonify({"ok": True, "signals": signals})


@app.route("/api/scanner/smart_money", methods=["GET"])
def scanner_smart_money_route():
    """
    Returns the four Smart Money Dashboard sections (Section 10):
      A. Bottom Accumulation
      B. Top Distribution
      C. BUY / SELL Confirmed
      D. Watchlist
    """
    with STATE_LOCK:
        eng = STATE.get("scanner")
    if not eng:
        return jsonify({"ok": True, "smart_money": {
            "bottom_accumulation": [],
            "top_distribution": [],
            "confirmed": [],
            "watchlist": [],
        }})
    data = eng.get_smart_money_dashboard()
    return jsonify({"ok": True, "smart_money": data})


@app.route("/api/scanner/sustained_breakout", methods=["GET"])
def scanner_sustained_breakout_route():
    """
    Returns the Strategy F 3-Panel Dashboard:
      1. Market Trend
      2. Derivatives Confirmation
      3. Trading Signal
    Query parameters:
      - ?symbol=... or ?ticker=... (default NIFTY)
      - ?scenario=bullish|bearish|neutral|dynamic|safety (for testing all market cases)
      - ?tick=0, 1, 2... (offset for dynamic score testing)
    """
    req_sym = (request.args.get("symbol") or request.args.get("ticker") or "NIFTY").strip().upper()
    req_scenario = (request.args.get("scenario") or "").strip().lower()
    tick_offset = request.args.get("tick", default=None, type=float)

    # Explicit scenario requested for QA/Testing all cases
    if req_scenario:
        from sustained_breakout_strategy import generate_strategy_f_scenario
        data = generate_strategy_f_scenario(scenario=req_scenario, symbol=req_sym, tick_offset=tick_offset)
        return jsonify({"ok": True, "data": data, "scenario": req_scenario})

    with STATE_LOCK:
        eng = STATE.get("scanner")
        client = STATE.get("kite_client")
        logged_in = STATE.get("logged_in")

    if logged_in and client:
        try:
            if eng:
                data = eng.get_or_eval_sustained_breakout(max_age_seconds=4, symbol=req_sym)
            else:
                from scanner_engine import evaluate_strategy_f_standalone
                data = evaluate_strategy_f_standalone(client, spot_symbol=req_sym)
            if data and data.get("trading_signal", {}).get("confidence_score") is not None:
                return jsonify({"ok": True, "data": data})
        except Exception as exc:
            pass

    # Dynamic fallback when Kite is not logged in / off-market simulation
    from sustained_breakout_strategy import generate_strategy_f_scenario
    data = generate_strategy_f_scenario(scenario="dynamic", symbol=req_sym, tick_offset=tick_offset)
    return jsonify({"ok": True, "data": data, "simulation": True})


@app.route("/api/scanner/gti", methods=["GET"])
def scanner_gti_route():
    """
    Returns Strategy G: GTI Smart Money Trader Dashboard:
      - Candle Color Classification (Yellow, Blue, Black, Green, Red)
      - Smart Signal Engine (BUY CALL, BUY PUT, ACCUMULATION, WAIT)
      - Option Suggestion Engine (ATM CE/PE, Delta, IV, SL, Target)
      - Risk Management (1:2 Target, 20-25% Option SL, Trailing SL)
      - Fake Signal Filters (Time Window, ATR Volatility, Volume, ADX Trend)
    Query parameters:
      - ?symbol=... (default NIFTY)
      - ?scenario=dynamic|call|put|yellow|sideways (for simulation & interactive QA)
      - ?tick=offset
    """
    req_sym = (request.args.get("symbol") or request.args.get("ticker") or "NIFTY").strip().upper()
    req_scenario = (request.args.get("scenario") or "").strip().lower()
    tick_offset = request.args.get("tick", default=None, type=float)

    if req_scenario:
        import gti_indicator
        data = gti_indicator.generate_gti_scenario(scenario=req_scenario, symbol=req_sym, tick_offset=tick_offset)
        return jsonify({"ok": True, "data": data, "scenario": req_scenario})

    with STATE_LOCK:
        eng = STATE.get("scanner")
        client = STATE.get("kite_client")
        logged_in = STATE.get("logged_in")

    if logged_in and client:
        try:
            if eng:
                dash = eng.get_or_eval_gti(max_age_seconds=4, symbol=req_sym)
                if dash and (dash.get("gti_status", {}).get("current_candle_color") or dash.get("current_candle_color")):
                    return jsonify({"ok": True, "data": dash})
            else:
                from scanner_engine import evaluate_strategy_g_standalone
                data = evaluate_strategy_g_standalone(client, spot_symbol=req_sym)
                if data and (data.get("gti_status", {}).get("current_candle_color") or data.get("current_candle_color")):
                    return jsonify({"ok": True, "data": data})
        except Exception:
            pass

    import gti_indicator
    data = gti_indicator.generate_gti_scenario(scenario="dynamic", symbol=req_sym, tick_offset=tick_offset)
    return jsonify({"ok": True, "data": data, "simulation": True})


@app.route("/api/scanner/clear", methods=["POST"])
def scanner_clear_route():
    """Clear in-memory signals and seen keys."""
    with STATE_LOCK:
        eng = STATE.get("scanner")
        if eng:
            eng.clear_signals()
    return jsonify({"ok": True, "message": "Signals cleared"})


@app.route("/api/scanner/resolve_atm", methods=["GET"])
def scanner_resolve_atm():
    symbol = request.args.get("symbol", "").strip().upper()
    option_type = request.args.get("option_type", "CE").strip().upper()
    spot_price = request.args.get("spot_price", type=float)
    if not symbol:
        return jsonify({"ok": False, "error": "symbol required"}), 400

    with STATE_LOCK:
        kite_client = STATE.get("kite_client")
    if not kite_client:
        import gti_indicator
        sp = spot_price or (62400.0 if "BANK" in symbol else (28000.0 if "FIN" in symbol else 25250.0))
        calc = gti_indicator.calculate_atm_option(sp, symbol=symbol, signal_type=("BUY_CALL" if option_type == "CE" else "BUY_PUT"))
        step = 100 if "BANK" in symbol else 50
        strike = round(sp / step) * step
        lot_size = 30 if "BANK" in symbol else (65 if "FIN" in symbol else 75)
        return jsonify({
            "ok": True,
            "atm_option": {
                "symbol": f"{symbol} {int(strike)} {option_type}",
                "strike": float(strike),
                "option_type": option_type,
                "expiry": "Current Expiry",
                "ltp": calc.get("estimated_ltp", 410.0),
                "lot_size": lot_size,
                "spot_price": sp,
                "futures_symbol": f"{symbol} FUT",
            }
        })

    try:
        atm = scanner_mod.resolve_atm_option(kite_client, symbol, option_type, spot_price=spot_price, log=print)
        if atm:
            return jsonify({"ok": True, "atm_option": atm})
        return jsonify({"ok": False, "error": f"could not resolve ATM option for {symbol}"}), 404
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(exc), "traceback": traceback.format_exc()}), 500


@app.route("/api/accounts", methods=["GET", "POST"])
def accounts_route():
    """List all configured Kite accounts with live session & MTM summary, or add/update an account."""
    if request.method == "POST":
        incoming = request.get_json(force=True, silent=True) or {}
        if not incoming.get("id") and (not incoming.get("api_key") or not incoming.get("api_secret")):
            return jsonify({"ok": False, "error": "api_key and api_secret are required for a new Kite account"}), 400
        acc = account_manager.add_or_update_account(incoming, load_settings, save_settings)
        masked = {k: v for k, v in acc.items() if k != "api_secret"}
        masked["has_secret"] = bool(acc.get("api_secret"))
        return jsonify({"ok": True, "account": masked})

    accounts = account_manager.load_accounts(load_settings)
    out = []
    with STATE_LOCK:
        acc_map = dict(STATE.get("accounts", {}))
        primary_client = STATE.get("kite_client")
        primary_logged = STATE.get("logged_in", False)

    for acc in accounts:
        aid = acc.get("id", "primary")
        rt = acc_map.get(aid, {})
        is_logged = bool(rt.get("logged_in")) or (aid == "primary" and primary_logged and primary_client is not None)
        user_name = rt.get("user_name") or (getattr(primary_client, "_user_name", None) if aid == "primary" else None) or acc.get("label")
        g = rt.get("guardian") or (STATE.get("guardian") if aid == "primary" else None)
        g_state = g.get_state() if g is not None else {}
        out.append({
            "id": aid,
            "label": acc.get("label", aid),
            "api_key": acc.get("api_key", ""),
            "has_secret": bool(acc.get("api_secret")),
            "lot_multiplier": int(acc.get("lot_multiplier", 1)),
            "dry_run": bool(acc.get("dry_run", True)),
            "enabled": bool(acc.get("enabled", True)),
            "logged_in": is_logged,
            "user_name": user_name if is_logged else None,
            "current_mtm": g_state.get("current_mtm", 0.0),
            "day_total_pnl": g_state.get("day_total_pnl", 0.0),
            "open_count": g_state.get("open_count", 0),
            "closed_count": g_state.get("closed_count", 0),
            "max_loss": acc.get("max_loss", 2500.0),
            "trailing_activation": acc.get("trailing_activation", 1500.0),
            "trailing_gap": acc.get("trailing_gap", 750.0),
            "per_pos_sl_pct": acc.get("per_pos_sl_pct", 25.0),
        })
    return jsonify({"ok": True, "accounts": out})


@app.route("/api/accounts/<account_id>", methods=["DELETE"])
def delete_account_route(account_id):
    """Remove a secondary Kite account and stop its guardian."""
    if account_id == "primary":
        return jsonify({"ok": False, "error": "Cannot delete primary account"}), 400
    with STATE_LOCK:
        rt = STATE["accounts"].pop(account_id, None)
    if rt and rt.get("guardian"):
        try:
            rt["guardian"].stop()
        except Exception:
            pass
    deleted = account_manager.delete_account(account_id, load_settings)
    if not deleted:
        return jsonify({"ok": False, "error": "Account not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/scanner/order", methods=["POST"])
def scanner_order():
    """
    Trigger an order on one or multiple logged-in Kite accounts concurrently.
    Supports `account_ids: ["primary", "acc_2"]` (or `"ALL"`), `account_quantities: {id: qty}`,
    and `use_multiplier: true`. Defaults to `["primary"]` for backward compatibility.
    """
    incoming = request.get_json(force=True, silent=True) or {}
    tradingsymbol = incoming.get("tradingsymbol", "").strip()
    exchange = incoming.get("exchange", "NFO").strip().upper()
    transaction_type = incoming.get("transaction_type", "BUY").strip().upper()
    settings = load_settings()
    product = (incoming.get("product") or settings.get("default_product", "MIS")).strip().upper()
    try:
        base_quantity = int(incoming.get("quantity", 0))
    except (ValueError, TypeError):
        base_quantity = 0

    if not tradingsymbol:
        return jsonify({"ok": False, "error": "tradingsymbol is required"}), 400
    if base_quantity <= 0:
        return jsonify({"ok": False, "error": "valid positive quantity is required"}), 400

    req_accounts = incoming.get("account_ids")
    acc_quantities = incoming.get("account_quantities") or {}
    use_multiplier = bool(incoming.get("use_multiplier", True))
    all_configured = account_manager.load_accounts(load_settings)
    acc_cfg_map = {a["id"]: a for a in all_configured}

    with STATE_LOCK:
        state_accounts = dict(STATE.get("accounts", {}))
        primary_client = STATE.get("kite_client")
        primary_logged = STATE.get("logged_in", False)

    # Resolve target account IDs
    if req_accounts == "ALL":
        target_ids = [a["id"] for a in all_configured if a.get("enabled", True)]
    elif isinstance(req_accounts, list) and len(req_accounts) > 0:
        target_ids = [str(x).strip() for x in req_accounts if str(x).strip()]
    else:
        target_ids = ["primary"]

    # Build execution list of (acc_id, client, acc_cfg, qty)
    exec_targets = []
    for aid in target_ids:
        rt = state_accounts.get(aid, {})
        client = rt.get("client") or (primary_client if aid == "primary" else None)
        is_logged = bool(rt.get("logged_in")) or (aid == "primary" and primary_logged and primary_client is not None)
        if not is_logged or client is None:
            continue
        acc_cfg = acc_cfg_map.get(aid, {"id": aid, "label": aid, "dry_run": settings.get("dry_run", True), "lot_multiplier": 1})
        client.settings["dry_run"] = bool(acc_cfg.get("dry_run", settings.get("dry_run", True)))
        if aid in acc_quantities:
            qty = int(acc_quantities[aid])
        elif use_multiplier and len(target_ids) > 1:
            qty = base_quantity * max(1, int(acc_cfg.get("lot_multiplier", 1)))
        else:
            qty = base_quantity
        if qty > 0:
            exec_targets.append((aid, client, acc_cfg, qty))

    if not exec_targets:
        return jsonify({"ok": False, "error": "No logged-in target Kite account available. Please login first."}), 400

    log = _make_log()

    def _exec_one(target_tuple):
        aid, client, acc_cfg, qty = target_tuple
        label = acc_cfg.get("label", aid)
        try:
            res = client.place_market_order(
                tradingsymbol=tradingsymbol,
                exchange=exchange,
                transaction_type=transaction_type,
                quantity=qty,
                product=product,
            )
            is_dry = bool(res.get("dry_run", False))
            order_id = res.get("order_id", "DRY_RUN_ORDER")
            protection_summary = {}
            try:
                g = _ensure_guardian_started(client, account_id=aid)
                if g:
                    g.register_order(
                        tradingsymbol=tradingsymbol,
                        exchange=exchange,
                        transaction_type=transaction_type,
                        quantity=qty,
                        product=product,
                        dry_run=is_dry,
                    )
                    protection_summary = g.get_config()
            except Exception as g_exc:
                print(f"[scanner] Guardian register_order note ({aid}): {g_exc!r}")

            try:
                log(f"[scanner] [{label}] {'[DRY RUN] ' if is_dry else '[LIVE] '}Order executed: {transaction_type} {qty} x {tradingsymbol} ({exchange}/{product}) -> ID: {order_id}")
                socketio.emit("scanner_order_placed", {**res, "account_id": aid, "label": label})
            except Exception:
                pass
            return {"ok": True, "account_id": aid, "label": label, "result": res, "protection": protection_summary}
        except Exception as exc:  # noqa: BLE001
            log(f"[scanner] [{label}] Order placement failed for {tradingsymbol}: {exc!r}")
            return {"ok": False, "account_id": aid, "label": label, "error": str(exc)}

    if len(exec_targets) == 1:
        per_results = [_exec_one(exec_targets[0])]
    else:
        with ThreadPoolExecutor(max_workers=min(8, len(exec_targets))) as pool:
            per_results = list(pool.map(_exec_one, exec_targets))

    succeeded = [r for r in per_results if r.get("ok")]
    if not succeeded:
        return jsonify({"ok": False, "error": per_results[0].get("error", "Order failed"), "results": per_results}), 500

    first_ok = succeeded[0]
    return jsonify({
        "ok": True,
        "result": first_ok["result"],
        "protection": first_ok.get("protection", {}),
        "results": per_results,
    })


def _get_guardian_for_account(account_id="primary"):
    aid = account_id or "primary"
    with STATE_LOCK:
        rt = STATE["accounts"].get(aid, {})
        client = rt.get("client") or (STATE.get("kite_client") if aid == "primary" else None)
        g = rt.get("guardian") or (STATE.get("guardian") if aid == "primary" else None)
        logged = rt.get("logged_in") or (STATE.get("logged_in") if aid == "primary" else False)
    if not logged or client is None:
        return None, None
    if g is None:
        g = _ensure_guardian_started(client, account_id=aid)
    return client, g


@app.route("/api/guardian/status", methods=["GET"])
def guardian_status_route():
    """Returns live Kite positions, combined MTM, Trailing SL, and risk guardian state across accounts."""
    req_acc = (request.args.get("account_id") or "primary").strip()
    accounts_cfg = account_manager.load_accounts(load_settings)
    per_accounts = []
    combined_mtm = 0.0
    combined_day_pnl = 0.0
    combined_peak = 0.0
    combined_open = 0
    combined_closed = 0

    for acc in accounts_cfg:
        aid = acc.get("id", "primary")
        client, g = _get_guardian_for_account(aid)
        if g is not None:
            st = g.get_state()
            combined_mtm += float(st.get("current_mtm") or 0.0)
            combined_day_pnl += float(st.get("day_total_pnl") or 0.0)
            combined_peak += float(st.get("peak_mtm") or 0.0)
            combined_open += int(st.get("open_count") or 0)
            combined_closed += int(st.get("closed_count") or 0)
            per_accounts.append({
                "account_id": aid,
                "label": acc.get("label", aid),
                "logged_in": True,
                "user_name": getattr(client, "_user_name", acc.get("label", aid)),
                **st,
            })

    if not per_accounts:
        return jsonify({
            "ok": False,
            "logged_in": False,
            "positions": [],
            "closed_positions": [],
            "accounts": [],
            "combined": {"current_mtm": 0.0, "day_total_pnl": 0.0, "peak_mtm": 0.0, "open_count": 0, "closed_count": 0},
            "config": position_guardian.DEFAULT_GUARDIAN_CONFIG,
        })

    if req_acc == "all":
        all_open = []
        all_closed = []
        all_events = []
        for a in per_accounts:
            for p in a.get("positions", []):
                all_open.append({**p, "account_id": a["account_id"], "account_label": a["label"]})
            for cp in a.get("closed_positions", []):
                all_closed.append({**cp, "account_id": a["account_id"], "account_label": a["label"]})
            for ev in a.get("events", []):
                all_events.append({**ev, "message": f"[{a['label']}] {ev['message']}"})
        base = dict(per_accounts[0])
        base["positions"] = all_open
        base["closed_positions"] = all_closed
        base["open_count"] = combined_open
        base["closed_count"] = combined_closed
        base["current_mtm"] = round(combined_mtm, 2)
        base["day_total_pnl"] = round(combined_day_pnl, 2)
        base["peak_mtm"] = round(combined_peak, 2)
        base["events"] = all_events[:10]
    else:
        selected = next((a for a in per_accounts if a["account_id"] == req_acc), per_accounts[0])
        base = dict(selected)

    return jsonify({
        "ok": True,
        "logged_in": True,
        **base,
        "accounts": per_accounts,
        "combined": {
            "current_mtm": round(combined_mtm, 2),
            "day_total_pnl": round(combined_day_pnl, 2),
            "peak_mtm": round(combined_peak, 2),
            "open_count": combined_open,
            "closed_count": combined_closed,
        },
    })


@app.route("/api/guardian/config", methods=["POST"])
def guardian_config_route():
    """Updates MTM Stop Loss, Trailing Stop Loss, and Per-Position SL rules for an account."""
    incoming = request.get_json(force=True, silent=True) or {}
    acc_id = (incoming.get("account_id") or "primary").strip()
    if acc_id == "all":
        acc_id = "primary"
    client, g = _get_guardian_for_account(acc_id)
    if g is not None:
        state = g.update_config(incoming)
        return jsonify({"ok": True, "account_id": acc_id, **state})
    # Offline settings fallback
    updated = account_manager.add_or_update_account({**incoming, "id": acc_id}, load_settings, save_settings)
    return jsonify({"ok": True, "account_id": acc_id, "config": updated})


@app.route("/api/guardian/exit_position", methods=["POST"])
def guardian_exit_position_route():
    """Squares off a single open position immediately on the specified account."""
    incoming = request.get_json(force=True, silent=True) or {}
    acc_id = (incoming.get("account_id") or "primary").strip()
    client, g = _get_guardian_for_account(acc_id)
    if not client or not g:
        return jsonify({"ok": False, "error": f"Account [{acc_id}] is not logged in"}), 400
    key = incoming.get("key", "")
    sym = incoming.get("tradingsymbol", "")
    st = g.get_state()
    target_pos = None
    for p in st.get("positions", []):
        if (key and p.get("key") == key) or (sym and p.get("tradingsymbol") == sym):
            target_pos = p
            break
    if not target_pos:
        return jsonify({"ok": False, "error": "Position not found or already closed"}), 404
    res = g.square_off_position(target_pos, reason="USER_MANUAL_EXIT")
    return jsonify(res)


@app.route("/api/guardian/exit_all", methods=["POST"])
def guardian_exit_all_route():
    """Emergency square-off of open positions for one account or ALL connected accounts."""
    incoming = request.get_json(force=True, silent=True) or {}
    acc_id = (incoming.get("account_id") or "all").strip()
    if acc_id == "all":
        accounts_cfg = account_manager.load_accounts(load_settings)
        total_exited = 0
        all_res = []
        for acc in accounts_cfg:
            aid = acc.get("id", "primary")
            client, g = _get_guardian_for_account(aid)
            if g is not None:
                r = g.square_off_all(reason="USER_PANIC_SQUARE_OFF_ALL", trigger_type="MANUAL_EXIT_ALL")
                total_exited += int(r.get("exited") or 0)
                all_res.append({"account_id": aid, **r})
        if not all_res:
            return jsonify({"ok": False, "error": "Please login first"}), 400
        return jsonify({"ok": True, "exited": total_exited, "results": all_res})

    client, g = _get_guardian_for_account(acc_id)
    if not client or not g:
        return jsonify({"ok": False, "error": "Please login first"}), 400
    res = g.square_off_all(reason="USER_PANIC_SQUARE_OFF_ALL", trigger_type="MANUAL_EXIT_ALL")
    return jsonify(res)


@app.route("/api/scanner/backtest", methods=["GET", "POST"])
def scanner_backtest_route():
    """
    Runs historical backtest for Strategy D (EMA 50/150/200 Trend & Reversal).
    Strictly filters to F&O universe — non-futures stocks are excluded.
    """
    incoming = request.get_json(force=True, silent=True) if request.method == "POST" else request.args
    incoming = incoming or {}

    with STATE_LOCK:
        kite_client = STATE.get("kite_client")

    lookback_days = int(incoming.get("days", 180))
    symbols_arg = incoming.get("symbols")
    if isinstance(symbols_arg, str):
        symbols = [s.strip().upper() for s in symbols_arg.split(",") if s.strip()]
    elif isinstance(symbols_arg, list):
        symbols = [str(s).strip().upper() for s in symbols_arg if str(s).strip()]
    else:
        symbols = ["ALL_FNO"]

    ema_fast = int(incoming.get("ema_fast", 50))
    ema_mid = int(incoming.get("ema_mid", 150))
    ema_slow = int(incoming.get("ema_slow", 200))
    require_breakout = bool(incoming.get("require_breakout", True))
    stop_loss_pct = float(incoming.get("stop_loss_pct", 0.03))
    target_pct = float(incoming.get("target_pct", 0.06))
    max_lot_size = int(incoming.get("max_lot_size", 2500))

    log = _make_log()
    log(f"[scanner] Starting backtest for Strategy D (days={lookback_days}, lot_size<{max_lot_size})...")

    try:
        results = scanner_backtest.run_fno_backtest(
            kite_client=kite_client,
            symbols=symbols,
            lookback_days=lookback_days,
            ema_fast=ema_fast,
            ema_mid=ema_mid,
            ema_slow=ema_slow,
            require_prev_day_breakout=require_breakout,
            stop_loss_pct=stop_loss_pct,
            target_pct=target_pct,
            max_lot_size=max_lot_size,
            log=log,
        )
        log(f"[scanner] Backtest complete: {results['metrics']['total_trades']} trades, "
            f"{results['metrics']['win_rate_pct']}% win rate, profit factor: {results['metrics']['profit_factor']}")
        return jsonify(results)
    except Exception as exc:  # noqa: BLE001
        log(f"[scanner] Backtest error: {exc!r}")
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Startup session restore
# ---------------------------------------------------------------------------

def _try_restore_session():
    accounts = account_manager.load_accounts(load_settings)
    for acc in accounts:
        acc_id = acc.get("id", "primary")
        acc_settings = account_manager.make_account_settings_loader(acc_id, load_settings)()
        if not acc_settings.get("kite_api_key") or not acc_settings.get("kite_api_secret"):
            continue
        cached_token = load_cached_access_token(acc_id)
        if not cached_token:
            continue
        try:
            client = KiteClient(acc_settings, log=print, account_id=acc_id)
            client.kite.set_access_token(cached_token)
            prof = client.kite.profile()
            user_name = prof.get("user_name") or prof.get("user_id") or "Kite User"
            client._user_name = user_name
            with STATE_LOCK:
                if acc_id == "primary":
                    STATE["kite_client"] = client
                    STATE["logged_in"] = True
            _ensure_guardian_started(client, account_id=acc_id)
            print(f"[app] restored today's Kite session [{acc_id}] for {user_name} — no need to log in again")
        except Exception as exc:  # noqa: BLE001
            print(f"[app] cached session for [{acc_id}] is invalid/expired ({exc!r}) — Login will be needed")
            if acc_id == "primary":
                with STATE_LOCK:
                    STATE["kite_client"] = None
                    STATE["logged_in"] = False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    os.makedirs(REPORTS_DIR, exist_ok=True)
    _try_restore_session()
    _start_port_5000_bridge()
    host = os.environ.get("HOST", "0.0.0.0")
    local_url = f"http://127.0.0.1:{PORT}"
    threading.Timer(1.2, lambda: webbrowser.open(local_url)).start()
    print(f"[app] starting StockEdge Options dashboard at {local_url} (LAN: http://192.168.1.43:{PORT})")
    socketio.run(app, host=host, port=PORT, allow_unsafe_werkzeug=True)
