"""
Pref.trade MCP client - fetches military aircraft via aviation.get_adsb_military.

Uses stdlib urllib only. JSON-RPC 2.0 over HTTP to https://pref.trade/mcp.

Pref uses two call conventions:
- Direct tools (e.g. preference_account_status): params.name = tool_name
- Dotted tool_refs (e.g. aviation.get_adsb_military): params.name = 'call_tool',
  tool_ref and arguments nested inside params.arguments
"""

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PREF_MCP_ENDPOINT = "https://pref.trade/mcp"
PREF_CREDENTIALS_PATH = Path.home() / ".config" / "preference" / "credentials.json"
_request_id = 0


def _next_id():
    global _request_id
    _request_id += 1
    return _request_id


def _load_api_key():
    """Load pref.trade API key from env or the standard agent credentials file."""
    for env_name in ("PREF_API_KEY", "PREFERENCE_API_KEY"):
        api_key = os.environ.get(env_name, "").strip()
        if api_key:
            return api_key

    try:
        data = json.loads(PREF_CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""

    return str(data.get("api_key") or "").strip()


def _call_tool(tool_name, arguments=None):
    """Call a pref MCP tool via JSON-RPC 2.0. Returns parsed result or None.

    Dotted tool names (containing '.') use the call_tool meta-pattern.
    Direct tool names use the standard MCP tools/call pattern.
    """
    api_key = _load_api_key()
    if not api_key:
        print("  [pref] PREF_API_KEY not set and no ~/.config/preference/credentials.json key found - skipping pref call")
        return None

    if "." in tool_name:
        params = {
            "name": "call_tool",
            "arguments": {"tool_ref": tool_name, "arguments": arguments or {}},
        }
    else:
        params = {"name": tool_name, "arguments": arguments or {}}

    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": _next_id(),
        "method": "tools/call",
        "params": params,
    }).encode()

    req = Request(
        PREF_MCP_ENDPOINT,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "simmer-sdk/milaircraft-tracker",
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            obj = json.loads(raw)
            if "error" in obj:
                print(f"  [pref] MCP error: {obj['error'].get('message', obj['error'])}")
                return None
            content = obj.get("result", {}).get("content", [])
            for item in content:
                if item.get("type") == "text":
                    return json.loads(item["text"])
            return None
    except HTTPError as exc:
        print(f"  [pref] HTTP {exc.code}: {exc.reason}")
        return None
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"  [pref] Request failed: {exc}")
        return None


def get_military_aircraft(limit=500, offset=0):
    """Fetch all military-tagged aircraft currently visible in ADS-B."""
    result = _call_tool("aviation.get_adsb_military", {"limit": limit, "offset": offset})
    if result is None:
        return []
    return result.get("data", result.get("ac", []))


def get_account_status():
    """Check pref account status."""
    return _call_tool("preference_account_status", {})
