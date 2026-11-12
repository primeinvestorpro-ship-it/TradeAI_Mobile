"""
Unit tests for Kite auth callback handling in app.py.
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest
from unittest.mock import MagicMock, patch
import app


@pytest.fixture
def client():
    app.app.config["TESTING"] = True
    with app.app.test_client() as c:
        yield c


def test_kite_callback_missing_token(client):
    res = client.get("/kite/callback?status=error&message=User+cancelled")
    assert res.status_code == 400
    assert b"Kite Login Failed" in res.data
    assert b"User cancelled" in res.data


def test_kite_callback_success_exchange(client):
    with patch("app.load_settings", return_value={"kite_api_key": "k", "kite_api_secret": "s"}), \
         patch("app.KiteClient") as mock_kc_cls:
        mock_kc = MagicMock()
        mock_kc_cls.return_value = mock_kc

        # Reset state
        with app.STATE_LOCK:
            app.STATE["logged_in"] = False
            app.STATE["kite_client"] = None

        res = client.get("/kite/callback?action=login&type=login&status=success&request_token=test_req_tok_123")
        assert res.status_code == 200
        assert b"Kite Login Successful" in res.data
        mock_kc.complete_login.assert_called_once_with("test_req_tok_123")
        with app.STATE_LOCK:
            assert app.STATE["logged_in"] is True


def test_kite_callback_token_exchange_failure(client):
    with patch("app.load_settings", return_value={"kite_api_key": "k", "kite_api_secret": "s"}), \
         patch("app.KiteClient") as mock_kc_cls:
        mock_kc = MagicMock()
        mock_kc.complete_login.side_effect = Exception("Invalid token or secret")
        mock_kc_cls.return_value = mock_kc

        with app.STATE_LOCK:
            app.STATE["logged_in"] = False
            app.STATE["kite_client"] = None

        res = client.get("/kite/callback?request_token=bad_tok")
        assert res.status_code == 400
        assert b"Kite Login Failed" in res.data
        assert b"Invalid token or secret" in res.data


def test_root_callback_success_exchange(client):
    """When Kite console Redirect URL is set to http://127.0.0.1:5050/ (root),
    Kite redirects to /?request_token=... - this must exchange token and redirect cleanly."""
    with patch("app.load_settings", return_value={"kite_api_key": "k", "kite_api_secret": "s"}), \
         patch("app.KiteClient") as mock_kc_cls:
        mock_kc = MagicMock()
        mock_kc_cls.return_value = mock_kc

        with app.STATE_LOCK:
            app.STATE["logged_in"] = False
            app.STATE["kite_client"] = None

        res = client.get("/?action=login&type=login&status=success&request_token=root_req_tok_456")
        assert res.status_code == 302
        assert res.headers["Location"] == "/"
        mock_kc.complete_login.assert_called_once_with("root_req_tok_456")
        with app.STATE_LOCK:
            assert app.STATE["logged_in"] is True
