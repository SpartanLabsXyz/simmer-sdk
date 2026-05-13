"""
Unit tests for simmer_sdk/version_check.py.

All network calls are mocked — pure unit tests, no server needed.
"""
import warnings
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(status_code: int, json_body: dict):
    """Return a mock requests.Session whose .get() returns the given response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_body
    mock_resp.raise_for_status.return_value = None  # no-op for 200

    session = MagicMock()
    session.get.return_value = mock_resp
    return session


def _make_error_session(exc):
    """Return a mock session whose .get() raises the given exception."""
    session = MagicMock()
    session.get.side_effect = exc
    return session


# ---------------------------------------------------------------------------
# check_server_version_compatibility
# ---------------------------------------------------------------------------

class TestCheckServerVersionCompatibility:
    from simmer_sdk.version_check import check_server_version_compatibility

    def test_ok_status_no_warning(self):
        session = _make_session(200, {"status": "ok", "message": "all good"})
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            from simmer_sdk.version_check import check_server_version_compatibility
            check_server_version_compatibility("https://api.simmer.markets", "0.17.0", session)
        assert len(w) == 0

    def test_deprecated_emits_deprecation_warning(self):
        msg = "simmer-sdk 0.13.0 is outdated. Upgrade recommended."
        session = _make_session(200, {"status": "deprecated", "message": msg})
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            from simmer_sdk.version_check import check_server_version_compatibility
            check_server_version_compatibility("https://api.simmer.markets", "0.13.0", session)
        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)
        assert "0.13.0" in str(w[0].message)

    def test_blocked_emits_deprecation_warning(self):
        msg = "simmer-sdk 0.9.2 is too old. Upgrade immediately."
        session = _make_session(200, {"status": "blocked", "message": msg})
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            from simmer_sdk.version_check import check_server_version_compatibility
            check_server_version_compatibility("https://api.simmer.markets", "0.9.2", session)
        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)
        assert "0.9.2" in str(w[0].message)

    def test_network_error_is_fail_quiet(self):
        """ConnectionError must not propagate — SDK startup must not break."""
        import requests
        session = _make_error_session(requests.exceptions.ConnectionError("unreachable"))
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            from simmer_sdk.version_check import check_server_version_compatibility
            # Should not raise
            check_server_version_compatibility("https://api.simmer.markets", "0.9.2", session)
        # No warning emitted either — network error is silently swallowed
        assert len(w) == 0

    def test_timeout_is_fail_quiet(self):
        import requests
        session = _make_error_session(requests.exceptions.Timeout("timed out"))
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            from simmer_sdk.version_check import check_server_version_compatibility
            check_server_version_compatibility("https://api.simmer.markets", "0.17.0", session)
        assert len(w) == 0

    def test_bad_json_is_fail_quiet(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.side_effect = ValueError("invalid json")
        session = MagicMock()
        session.get.return_value = mock_resp
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            from simmer_sdk.version_check import check_server_version_compatibility
            check_server_version_compatibility("https://api.simmer.markets", "0.9.0", session)
        assert len(w) == 0

    def test_http_error_is_fail_quiet(self):
        import requests
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
        session = MagicMock()
        session.get.return_value = mock_resp
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            from simmer_sdk.version_check import check_server_version_compatibility
            check_server_version_compatibility("https://api.simmer.markets", "0.9.0", session)
        assert len(w) == 0

    def test_correct_url_constructed(self):
        session = _make_session(200, {"status": "ok", "message": ""})
        from simmer_sdk.version_check import check_server_version_compatibility
        check_server_version_compatibility("https://api.simmer.markets/", "0.17.0", session)
        call_args = session.get.call_args
        url = call_args[0][0]
        # trailing slash stripped, path appended
        assert url == "https://api.simmer.markets/api/sdk/version-check"

    def test_sdk_version_passed_as_query_param(self):
        session = _make_session(200, {"status": "ok", "message": ""})
        from simmer_sdk.version_check import check_server_version_compatibility
        check_server_version_compatibility("https://api.simmer.markets", "0.9.1", session)
        call_kwargs = session.get.call_args[1]
        assert call_kwargs.get("params", {}).get("sdk_version") == "0.9.1"
