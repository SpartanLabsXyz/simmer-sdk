"""Tests for nansen_adapter.py — mocked, never calls the real Nansen API."""

import io
import json
import sys
import os
import urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from unittest.mock import patch, MagicMock
import pytest

import nansen_adapter as na


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fake_api_key():
    """Every test runs as if NANSEN_API_KEY were set."""
    with patch("nansen_adapter._api_key", return_value="test-key"):
        yield


def _mock_post(data):
    """Return a patch target that makes _post return `data`."""
    return patch("nansen_adapter._post", return_value=data)


def _mock_http(payload):
    """Patch urlopen to return `payload` (a JSON-serialisable object)."""
    body = json.dumps(payload).encode()

    class _Resp:
        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return patch("urllib.request.urlopen", return_value=_Resp())


def _http_error(code, detail=b'{"error":"boom"}'):
    return urllib.error.HTTPError(
        url="https://api.nansen.ai/api/v1/x", code=code,
        msg="err", hdrs=None, fp=io.BytesIO(detail),
    )


# ---------------------------------------------------------------------------
# pnl_by_market
# ---------------------------------------------------------------------------

def test_pnl_by_market_normalises_list_response():
    raw = [
        {
            "address": "0xABC",
            "owner_address": "0xOWNER",
            "side_held": "YES",
            "net_buy_cost_usd": 500.0,
            "unrealized_value_usd": 0.0,
            "total_pnl_usd": 300.0,
        }
    ]
    with _mock_post(raw):
        rows = na.pnl_by_market("42")

    assert len(rows) == 1
    r = rows[0]
    assert r["address"] == "0xABC"
    assert r["side_held"] == "yes"        # normalised to lower
    assert r["total_pnl_usd"] == 300.0


def test_pnl_by_market_normalises_wrapped_response():
    raw = {"data": [{"address": "0xXYZ", "side_held": "No", "net_buy_cost_usd": 100.0,
                     "total_pnl_usd": -50.0, "unrealized_value_usd": 0.0}]}
    with _mock_post(raw):
        rows = na.pnl_by_market("99")

    assert rows[0]["side_held"] == "no"
    assert rows[0]["total_pnl_usd"] == -50.0


# ---------------------------------------------------------------------------
# pnl_quality_score
# ---------------------------------------------------------------------------

def test_quality_score_basic():
    rows = [
        {"market_resolved": True, "net_buy_cost_usd": 100.0, "total_pnl_usd": 80.0, "question": "Q1"},
        {"market_resolved": True, "net_buy_cost_usd": 100.0, "total_pnl_usd": 90.0, "question": "Q2"},
        {"market_resolved": True, "net_buy_cost_usd": 100.0, "total_pnl_usd": 70.0, "question": "Q3"},
    ]
    result = na.pnl_quality_score(rows, min_resolved=3)
    assert result["win_rate"] == 1.0
    assert result["avg_roi"] == pytest.approx(0.8, abs=0.01)
    assert result["resolved_count"] == 3
    assert result["consistency"] is not None
    assert 0.0 <= result["consistency"] <= 1.0


def test_quality_score_insufficient_history():
    rows = [
        {"market_resolved": True, "net_buy_cost_usd": 100.0, "total_pnl_usd": 50.0, "question": "Q1"},
    ]
    result = na.pnl_quality_score(rows, min_resolved=3)
    assert result["avg_roi"] is None
    assert result["resolved_count"] == 1


def test_quality_score_no_resolved():
    rows = [
        {"market_resolved": False, "net_buy_cost_usd": 100.0, "total_pnl_usd": 0.0, "question": "Q1"},
    ]
    result = na.pnl_quality_score(rows)
    assert result["win_rate"] is None
    assert result["resolved_count"] == 0


def test_quality_score_mixed_wins_losses():
    rows = [
        {"market_resolved": True, "net_buy_cost_usd": 100.0, "total_pnl_usd": 50.0, "question": "Q1"},
        {"market_resolved": True, "net_buy_cost_usd": 100.0, "total_pnl_usd": -30.0, "question": "Q2"},
        {"market_resolved": True, "net_buy_cost_usd": 100.0, "total_pnl_usd": 20.0, "question": "Q3"},
    ]
    result = na.pnl_quality_score(rows, min_resolved=3)
    assert result["win_rate"] == pytest.approx(2/3, abs=0.01)
    assert result["resolved_count"] == 3


# ---------------------------------------------------------------------------
# compute_roi
# ---------------------------------------------------------------------------

def test_compute_roi_positive():
    assert na.compute_roi(100.0, 50.0) == pytest.approx(0.5)


def test_compute_roi_negative():
    assert na.compute_roi(100.0, -30.0) == pytest.approx(-0.3)


def test_compute_roi_zero_cost():
    assert na.compute_roi(0.0, 100.0) is None


def test_compute_roi_negative_cost():
    assert na.compute_roi(-10.0, 100.0) is None


# ---------------------------------------------------------------------------
# wallet_age_days
# ---------------------------------------------------------------------------

def test_wallet_age_days_recent():
    from datetime import datetime, timezone, timedelta
    three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    balances = [
        {"block_timestamp": three_days_ago, "value_usd": 1000.0, "token_symbol": "USDC"}
    ]
    age = na.wallet_age_days(balances)
    assert age is not None
    assert 2.5 < age < 3.5


def test_wallet_age_days_no_funded_records():
    balances = [
        {"block_timestamp": "2026-01-01T00:00:00Z", "value_usd": 0.0, "token_symbol": "USDC"}
    ]
    age = na.wallet_age_days(balances)
    assert age is None


def test_wallet_age_days_empty():
    assert na.wallet_age_days([]) is None


# ---------------------------------------------------------------------------
# _post transport: auth, retry, status-code mapping
# ---------------------------------------------------------------------------

def test_post_returns_parsed_json():
    with _mock_http({"data": [{"foo": "bar"}]}):
        data = na._post("prediction-market/address-summary", {"address": "0x1"})

    assert data == {"data": [{"foo": "bar"}]}


def test_post_sends_apikey_header_and_browser_ua():
    """Regression: the header must be `apiKey`, and the UA must not be urllib's
    default — Cloudflare 403s the default, and a missing key returns a 402."""
    captured = {}

    class _Resp:
        def read(self): return b'{"data": []}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.headers)
        captured["method"] = req.get_method()
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)
        return _Resp()

    with patch("urllib.request.urlopen", _fake_urlopen):
        na._post("prediction-market/address-summary", {"address": "0xabc"})

    # urllib title-cases header keys
    assert captured["headers"]["Apikey"] == "test-key"
    assert "Authorization" not in captured["headers"]
    assert "X-api-key" not in captured["headers"]
    assert "Mozilla/5.0" in captured["headers"]["User-agent"]
    assert captured["method"] == "POST"
    assert captured["url"].startswith("https://api.nansen.ai/api/v1/")
    assert captured["body"] == {"address": "0xabc"}


def test_post_without_api_key_raises_auth_error_before_sending():
    """A bare request comes back as a confusing 402 x402 paywall, so fail early."""
    with patch("nansen_adapter._api_key", return_value=""):
        with patch("urllib.request.urlopen") as mock_open:
            with pytest.raises(na.NansenAuthError):
                na._post("prediction-market/address-summary", {"address": "0x1"})
    mock_open.assert_not_called()


@pytest.mark.parametrize("code,exc", [
    (401, na.NansenAuthError),
    (402, na.NansenPaymentRequiredError),
    (403, na.NansenAccessDeniedError),
    (404, na.NansenRouteError),
    (422, na.NansenRequestError),
])
def test_post_maps_status_codes_to_distinct_exceptions(code, exc):
    """401/402/403/404 mean four different things and must not be conflated."""
    with patch("urllib.request.urlopen", side_effect=_http_error(code)):
        with pytest.raises(exc):
            na._post("prediction-market/address-summary", {"address": "0x1"})


@pytest.mark.parametrize("code", [401, 402, 403, 404, 422])
def test_post_does_not_retry_hard_failures(code):
    """Retrying these cannot change the outcome — and 403 would burn credits."""
    with patch("urllib.request.urlopen", side_effect=_http_error(code)) as mock_open:
        with patch("time.sleep"):
            with pytest.raises(na.NansenError):
                na._post("prediction-market/address-summary", {"address": "0x1"}, retries=3)

    assert mock_open.call_count == 1


def test_post_retries_transient_5xx():
    with patch("urllib.request.urlopen", side_effect=_http_error(500)) as mock_open:
        with patch("time.sleep"):  # skip backoff delay in tests
            with pytest.raises(na.NansenError):
                na._post("prediction-market/address-summary", {"address": "0x1"}, retries=2)

    assert mock_open.call_count == 2


def test_payment_required_is_not_an_access_problem():
    """402 means the request went out unauthenticated, NOT that quota ran out."""
    assert not issubclass(na.NansenPaymentRequiredError, na.NansenAccessDeniedError)
    assert not issubclass(na.NansenAccessDeniedError, na.NansenPaymentRequiredError)


def test_credits_exhausted_name_is_a_backcompat_alias():
    """The old name asserted a cause the API never reports; keep it working
    but make sure it is the same ambiguous 403 class."""
    assert na.NansenCreditsExhaustedError is na.NansenAccessDeniedError


def test_no_profiler_labels_wrapper():
    """Deliberately absent: unverified endpoint we've decided not to use.
    See README limitation 10 before re-adding."""
    assert not hasattr(na, "profiler_labels")


def test_account_is_a_free_get_and_takes_no_guard():
    """GET /account reports plan + remaining credits and must not be billed
    against a CreditGuard — it's the tool for telling a 403 apart."""
    captured = {}

    class _Resp:
        def read(self): return b'{"plan": "free", "credits_remaining": 27}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_urlopen(req, timeout=None):
        captured["method"] = req.get_method()
        captured["url"] = req.full_url
        captured["data"] = req.data
        return _Resp()

    with patch("urllib.request.urlopen", _fake_urlopen):
        result = na.account()

    assert result == {"plan": "free", "credits_remaining": 27}
    assert captured["method"] == "GET"
    assert captured["data"] is None
    assert captured["url"].endswith("/account")


# ---------------------------------------------------------------------------
# CreditGuard
# ---------------------------------------------------------------------------

def test_credit_guard_raises_when_budget_exhausted():
    guard = na.CreditGuard(max_calls=1)

    with _mock_http({"data": [{"a": 1}]}):
        na._post("prediction-market/pnl-by-market", {"market_id": "1"}, guard=guard)
        with pytest.raises(na.CreditGuardExceeded):
            na._post("prediction-market/pnl-by-market", {"market_id": "2"}, guard=guard)


def test_credit_guard_exceeded_is_a_nansen_error():
    assert issubclass(na.CreditGuardExceeded, na.NansenError)


def test_credit_guard_cache_hit_does_not_consume_budget():
    guard = na.CreditGuard(max_calls=1)

    with _mock_http({"data": [{"a": 1}]}) as mock_open:
        first = na._post("prediction-market/pnl-by-market", {"market_id": "1"}, guard=guard)
        second = na._post("prediction-market/pnl-by-market", {"market_id": "1"}, guard=guard)

    assert first == second == {"data": [{"a": 1}]}
    assert mock_open.call_count == 1  # only one real HTTP call


def test_credit_guard_cache_key_includes_body():
    """Same path, different body must not collide in the cache."""
    guard = na.CreditGuard(max_calls=5)

    with _mock_http({"data": []}) as mock_open:
        na._post("prediction-market/pnl-by-market", {"market_id": "1"}, guard=guard)
        na._post("prediction-market/pnl-by-market", {"market_id": "2"}, guard=guard)

    assert mock_open.call_count == 2
    assert guard.calls_made == 2


def test_credit_guard_cache_expires_after_ttl():
    guard = na.CreditGuard(max_calls=5, cache_ttl_s=0.0)  # expire immediately

    with _mock_http({"data": [{"a": 1}]}) as mock_open:
        na._post("prediction-market/pnl-by-market", {"market_id": "1"}, guard=guard)
        na._post("prediction-market/pnl-by-market", {"market_id": "1"}, guard=guard)

    assert mock_open.call_count == 2


def test_credit_guard_no_guard_means_unbounded_default():
    """Backward compatible: guard=None (the default) never raises/caches."""
    with _mock_http({"data": [{"a": 1}]}) as mock_open:
        na._post("prediction-market/pnl-by-market", {"market_id": "1"})
        na._post("prediction-market/pnl-by-market", {"market_id": "1"})

    assert mock_open.call_count == 2  # no cache without a guard


# ---------------------------------------------------------------------------
# address_summary
# ---------------------------------------------------------------------------

# Verified live payload — see artifacts/nansen/address_summary_raw.json.
VERIFIED_ADDRESS_SUMMARY = {
    "pagination": {"page": 1, "per_page": 10, "is_last_page": True},
    "data": [{
        "address": "0xac4a1fabdac2438d6afa2a9e8e83845310a0bf1e",
        "first_seen": "2026-02-23",
        "wallet_age_days": 154,
        "realized_pnl_usd": 12766.979439999992,
        "unrealized_pnl_usd": 243807.194637681,
        "total_pnl_usd": 256574.17407768097,
        "markets_won": 2541,
        "markets_traded": 6375,
        "win_rate": 0.39858823529411763,
        "p2p_tokens_sent": 7943718.163580999,
        "p2p_tokens_received": 9259098.775497003,
    }],
}


def test_address_summary_normalises_verified_live_shape():
    with _mock_post(VERIFIED_ADDRESS_SUMMARY):
        result = na.address_summary("0xac4a1fabdac2438d6afa2a9e8e83845310a0bf1e")

    assert result["win_rate"] == pytest.approx(0.3986, abs=1e-4)
    assert result["markets_traded"] == 6375
    assert result["markets_won"] == 2541
    assert result["wallet_age_days"] == 154
    assert result["first_seen"] == "2026-02-23"
    assert result["total_pnl_usd"] == pytest.approx(256574.17, abs=0.01)
    assert result["realized_pnl_usd"] == pytest.approx(12766.98, abs=0.01)
    assert result["p2p_tokens_received"] == pytest.approx(9259098.78, abs=0.01)


def test_address_summary_resolved_count_aliases_markets_traded():
    """Callers gate history depth on resolved_count; this endpoint only
    reports markets_traded, so the alias must track it."""
    with _mock_post(VERIFIED_ADDRESS_SUMMARY):
        result = na.address_summary("0xPROXY")

    assert result["resolved_count"] == 6375


def test_address_summary_avg_roi_not_offered_by_endpoint():
    with _mock_post(VERIFIED_ADDRESS_SUMMARY):
        assert na.address_summary("0xPROXY")["avg_roi"] is None


def test_address_summary_posts_to_prediction_market_route():
    captured = {}

    def _fake_post(path, body, **kw):
        captured["path"] = path
        captured["body"] = body
        return VERIFIED_ADDRESS_SUMMARY

    with patch("nansen_adapter._post", _fake_post):
        na.address_summary("0xPROXY")

    assert captured["path"] == "prediction-market/address-summary"
    assert captured["body"] == {"address": "0xPROXY"}


def test_address_summary_handles_empty_data():
    with _mock_post({"pagination": {}, "data": []}):
        result = na.address_summary("0xPROXY")

    assert result["resolved_count"] == 0
    assert result["win_rate"] is None
    assert result["address"] == "0xPROXY"


# ---------------------------------------------------------------------------
# get_proxy_address / get_owner_address
# ---------------------------------------------------------------------------

def test_get_proxy_address_prefers_explicit_field():
    leader = {"proxy_address": "0xPROXY", "address": "0xLEGACY"}
    assert na.get_proxy_address(leader) == "0xPROXY"


def test_get_proxy_address_falls_back_to_legacy_address():
    leader = {"address": "0xLEGACY"}
    assert na.get_proxy_address(leader) == "0xLEGACY"


def test_get_proxy_address_missing_returns_empty_string():
    assert na.get_proxy_address({}) == ""


def test_get_owner_address_no_fallback():
    """Unlike proxy, owner address has no fallback — profiler data keyed by
    the wrong wallet type silently returns the wrong wallet's history."""
    leader = {"address": "0xLEGACY", "proxy_address": "0xPROXY"}
    assert na.get_owner_address(leader) == ""


def test_get_owner_address_explicit_field():
    leader = {"owner_address": "0xOWNER"}
    assert na.get_owner_address(leader) == "0xOWNER"


def test_get_owner_address_reads_from_leaderboard_row():
    """owner_address rides along free on every pnl_by_market row, so callers
    shouldn't have to pass it in."""
    leader = {"proxy_address": "0xPROXY"}
    row = {"address": "0xPROXY", "owner_address": "0xOWNERFROMROW"}
    assert na.get_owner_address(leader, row) == "0xOWNERFROMROW"


def test_get_owner_address_prefers_explicit_over_row():
    leader = {"owner_address": "0xEXPLICIT"}
    row = {"owner_address": "0xFROMROW"}
    assert na.get_owner_address(leader, row) == "0xEXPLICIT"


@pytest.mark.parametrize("placeholder", [
    "0x", "0X", "0x0", "0x0000000000000000000000000000000000000000", "", None,
])
def test_owner_address_placeholders_treated_as_absent(placeholder):
    """~60% of live pnl_by_market rows carry "0x" rather than a real owner.
    Passing that to a profiler endpoint queries a nonexistent wallet and
    silently returns empty history, so it must resolve to ""."""
    assert na.owner_address_from_row({"owner_address": placeholder}) == ""
    assert na.get_owner_address({}, {"owner_address": placeholder}) == ""


def test_owner_address_from_row_returns_real_address():
    row = {"owner_address": "0xf59dbfaf900afab856fe71f6ca241d5f1d03f10d"}
    assert na.owner_address_from_row(row) == "0xf59dbfaf900afab856fe71f6ca241d5f1d03f10d"


def test_pnl_by_market_scrubs_owner_placeholder():
    raw = {"data": [{"address": "0xPROXY", "owner_address": "0x",
                     "side_held": "Yes", "net_buy_cost_usd": 1.0,
                     "total_pnl_usd": 2.0, "unrealized_value_usd": 0.0}]}
    with _mock_post(raw):
        rows = na.pnl_by_market("1")

    assert rows[0]["owner_address"] == ""
    assert rows[0]["address"] == "0xPROXY"


# --- Credit-cap validation (Greptile P1, simmer-sdk#306) ------------------
#
# A nonpositive budget is never what a caller means, and both caps spend the
# user's own Nansen credits. Before this guard, CreditGuard(max_calls=0) tripped
# on the very first request and surfaced as a traceback rather than the tagged
# CREDIT_GUARD_EXHAUSTED recovery path.

@pytest.mark.parametrize("bad", [0, -1, -40])
def test_credit_guard_rejects_nonpositive_budget(bad):
    with pytest.raises(ValueError, match="max_calls must be >= 1"):
        na.CreditGuard(max_calls=bad)


def test_credit_guard_accepts_a_budget_of_one():
    guard = na.CreditGuard(max_calls=1)
    assert guard.max_calls == 1
