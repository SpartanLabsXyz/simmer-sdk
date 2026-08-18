"""import_polymarket_wallet() adopts the env wallet on a sim-default client.

`from_env()` without venue sets _ignore_env_wallets=True, so the client has no
signer even when WALLET_PRIVATE_KEY is set. An explicit import call is
unambiguous intent to use that key — found by Adrian in the SIM-4646
acceptance run when the documented copy-paste snippet failed with
"private_key or ows_wallet required".
"""

import os
from unittest.mock import MagicMock, patch

from simmer_sdk.client import SimmerClient

_KEY = "0x" + "11" * 32


def _bare_client():
    """Client shaped like from_env() with the sim default: no signer configured."""
    client = SimmerClient.__new__(SimmerClient)
    client._agent_id = "test-agent-uuid"
    client._ows_wallet = None
    client._wallet_address = None
    client._private_key = None
    client.base_url = "https://api.simmer.markets"
    client.live = True
    client.venue = "sim"
    client._request = MagicMock()
    return client


def test_import_adopts_env_private_key():
    client = _bare_client()
    with patch.dict(os.environ, {"WALLET_PRIVATE_KEY": _KEY}, clear=False), \
         patch.object(client, "link_wallet",
                      return_value={"success": False, "error": "stop here"}) as mock_link:
        client.import_polymarket_wallet()
    assert client._private_key == _KEY
    assert client._wallet_address is not None
    mock_link.assert_called_once()


def test_import_prefers_env_ows_over_raw_key():
    client = _bare_client()
    with patch.dict(os.environ,
                    {"OWS_WALLET": "my-vault", "WALLET_PRIVATE_KEY": _KEY},
                    clear=False), \
         patch("simmer_sdk.ows_utils.get_ows_wallet_address",
               return_value="0x1234567890abcdef1234567890abcdef12345678"), \
         patch.object(client, "link_wallet",
                      return_value={"success": False, "error": "stop here"}):
        client.import_polymarket_wallet()
    assert client._ows_wallet == "my-vault"
    assert client._private_key is None


def test_import_does_not_override_explicit_signer():
    client = _bare_client()
    client._private_key = _KEY
    client._wallet_address = "0x3c7fa6cb352c6df150b14e16c635029a0b56aa0b"
    with patch.dict(os.environ, {"WALLET_PRIVATE_KEY": "0x" + "22" * 32}, clear=False), \
         patch.object(client, "link_wallet",
                      return_value={"success": False, "error": "stop here"}):
        client.import_polymarket_wallet()
    assert client._private_key == _KEY
