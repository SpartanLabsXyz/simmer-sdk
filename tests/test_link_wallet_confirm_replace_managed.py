"""Tests for the SIM-1580 confirm_replace_managed flag in client.link_wallet.

The flag pairs with a server-side guard on `/api/sdk/wallet/link` that
prevents silent overwrite of a managed wallet by simmer-sdk's
`_ensure_wallet_linked` auto-relink path. The intent split is:

  - Explicit `client.link_wallet()` → user-driven self-custody → flag True
  - Implicit `_ensure_wallet_linked` → auto-relink on local-key mismatch
    → flag False (so a stale WALLET_PRIVATE_KEY in a bot env after a
    managed-mode migration cannot silently displace the managed wallet
    — the relink fails loud with a 4xx instead).

These tests pin the contract so a future refactor can't quietly invert
either side and re-open the wongc305@ failure mode.
"""

import inspect
import re
from pathlib import Path

from simmer_sdk.client import SimmerClient


CLIENT_PATH = Path(__file__).parent.parent / "simmer_sdk" / "client.py"


def _client_source() -> str:
    return CLIENT_PATH.read_text()


# =============================================================================
# Method signature
# =============================================================================


def test_link_wallet_accepts_confirm_replace_managed():
    """Public method signature must accept the flag — without this, no
    caller can opt into replacement and the server-side guard becomes a
    permanent block on legitimate managed→external switches."""
    sig = inspect.signature(SimmerClient.link_wallet)
    assert "confirm_replace_managed" in sig.parameters, (
        "SimmerClient.link_wallet must accept confirm_replace_managed — "
        "removing it breaks the legitimate managed→external switch flow."
    )


def test_link_wallet_default_is_true():
    """Default MUST be True for explicit user calls. An explicit
    `client.link_wallet()` is a self-custody intent statement; defaulting
    False here would surprise users who switched to self-custody before
    SIM-1580 and break their existing flows on upgrade. The auto-relink
    path overrides to False (asserted below) — that's where the safe
    default lives, not on the public method."""
    sig = inspect.signature(SimmerClient.link_wallet)
    default = sig.parameters["confirm_replace_managed"].default
    assert default is True, (
        f"link_wallet() default for confirm_replace_managed must be True "
        f"(explicit user call = self-custody intent). Got {default!r}. "
        f"Defaulting False would break existing user flows on upgrade."
    )


# =============================================================================
# Wire format — flag must reach the server
# =============================================================================


def test_link_wallet_sends_flag_in_request_body():
    """The flag must be in the POST body — adding it to the signature but
    not the body would mean the server defaults the flag to False (its
    server-side default) and the explicit user call ALSO hits the guard."""
    src = _client_source()
    # Find the link_wallet method body and look at the json= payload.
    method_body = re.search(
        r"def link_wallet\([\s\S]*?(?=\n    def |\nclass )",
        src,
    )
    assert method_body, "Could not isolate link_wallet method body"
    body = method_body.group(0)

    # The /wallet/link POST must include confirm_replace_managed in json.
    payload = re.search(
        r'"/api/sdk/wallet/link",\s*\n\s*json\s*=\s*\{([\s\S]*?)\}',
        body,
    )
    assert payload, "Could not locate the /wallet/link POST json payload"
    payload_text = payload.group(1)

    assert "confirm_replace_managed" in payload_text, (
        "POST /api/sdk/wallet/link body must include confirm_replace_managed "
        "— otherwise the flag is dropped client-side and the server reverts "
        "to its safe default (False), blocking even legitimate user calls."
    )
    # Pinned to the parameter, not a hardcoded literal — so the explicit
    # default + auto-relink override both flow through correctly.
    assert re.search(
        r'"confirm_replace_managed"\s*:\s*confirm_replace_managed',
        payload_text,
    ), (
        "POST body must pass the parameter through (not a hardcoded True/False) "
        "so the auto-relink override actually reaches the server."
    )


# =============================================================================
# Auto-relink path overrides to False
# =============================================================================


def test_ensure_wallet_linked_passes_false():
    """The implicit auto-relink path must override to False. This is the
    LOAD-BEARING invariant of SIM-1580 — without this override, a stale
    WALLET_PRIVATE_KEY in a bot env after a managed-mode migration would
    still silently displace the managed wallet."""
    src = _client_source()
    # Isolate _ensure_wallet_linked.
    method = re.search(
        r"def _ensure_wallet_linked\([\s\S]*?(?=\n    def |\nclass )",
        src,
    )
    assert method, "Could not isolate _ensure_wallet_linked"
    body = method.group(0)

    # Inside the auto-link path it must call self.link_wallet with
    # confirm_replace_managed=False — explicit, no relying on default.
    call = re.search(
        r"self\.link_wallet\(\s*signature_type\s*=\s*0\s*,\s*"
        r"confirm_replace_managed\s*=\s*False\s*\)",
        body,
    )
    assert call, (
        "_ensure_wallet_linked must call self.link_wallet(signature_type=0, "
        "confirm_replace_managed=False) — explicit override is what protects "
        "managed accounts from silent overwrite. Removing this re-opens the "
        "SIM-1580 footgun."
    )
