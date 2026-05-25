import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "polymarket-mil-aircraft-tracker"
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


def test_fetch_military_aircraft_parses_response():
    """get_military_aircraft() returns list of aircraft dicts from pref MCP response."""
    import pref_client

    mock_response = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": json.dumps({
            "data": [
                {
                    "hex": "ae1234",
                    "flight": "DEATH11",
                    "lat": 26.5,
                    "lon": 56.2,
                    "alt_baro": 35000,
                    "t": "mil",
                    "squawk": "0100",
                },
                {
                    "hex": "ae5678",
                    "flight": "KNIFE22",
                    "lat": 27.0,
                    "lon": 55.8,
                    "alt_baro": 28000,
                    "t": "mil",
                    "squawk": "0200",
                },
            ],
            "pagination": {"total": 2, "has_more": False},
            "metadata": {"source": "adsb"},
        })}]},
    }).encode()

    with patch("pref_client.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        os.environ["PREF_API_KEY"] = "pref_agent_test"
        aircraft = pref_client.get_military_aircraft()

        assert len(aircraft) == 2
        assert aircraft[0]["hex"] == "ae1234"
        assert aircraft[0]["lat"] == 26.5
        assert aircraft[1]["flight"] == "KNIFE22"


def test_fetch_military_aircraft_handles_error():
    """get_military_aircraft() returns empty list on HTTP error."""
    import pref_client

    with patch("pref_client.urlopen") as mock_urlopen:
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError(
            "https://pref.trade/mcp", 429, "Rate limited", {}, None
        )
        os.environ["PREF_API_KEY"] = "pref_agent_test"
        aircraft = pref_client.get_military_aircraft()
        assert aircraft == []


def test_load_api_key_accepts_preference_alias():
    """PREFERENCE_API_KEY works for agents that follow pref.trade onboarding docs."""
    import pref_client

    with patch.dict(os.environ, {"PREFERENCE_API_KEY": "pref_agent_alias"}, clear=True):
        assert pref_client._load_api_key() == "pref_agent_alias"


def test_load_api_key_reads_standard_credentials_file(tmp_path):
    """Agents can store pref credentials outside .env and still run the skill."""
    import pref_client

    cred = tmp_path / "credentials.json"
    cred.write_text(json.dumps({"api_key": "pref_agent_file"}))

    with patch.dict(os.environ, {}, clear=True), patch.object(pref_client, "PREF_CREDENTIALS_PATH", cred):
        assert pref_client._load_api_key() == "pref_agent_file"
