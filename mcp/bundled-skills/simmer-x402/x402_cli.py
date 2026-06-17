#!/usr/bin/env python3
"""
Simmer x402 Payment Skill

Make x402 payments to access paid APIs using USDC on Base.
Wraps the official Coinbase x402 Python SDK.

Usage:
    python x402_cli.py balance                    # Check wallet balances
    python x402_cli.py fetch <url>                # Fetch with auto-payment
    python x402_cli.py fetch <url> --json         # Output raw JSON
    python x402_cli.py fetch <url> --dry-run      # Preview without paying
    python x402_cli.py fetch <url> --max 5.00     # Override max payment
    python x402_cli.py rpc <network> <method> [params]  # Quicknode RPC call

Requires:
    EVM_PRIVATE_KEY environment variable
    pip install x402[httpx,evm]
"""

import os
import sys
import json
import asyncio
import argparse
from pathlib import Path

# Force line-buffered stdout for non-TTY environments
sys.stdout.reconfigure(line_buffering=True)

# =============================================================================
# Configuration
# =============================================================================

def _load_config():
    """Load config with priority: config.json > env vars > defaults."""
    config_path = Path(__file__).parent / "config.json"
    file_cfg = {}
    if config_path.exists():
        try:
            with open(config_path) as f:
                file_cfg = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    return {
        "max_payment_usd": file_cfg.get("max_payment_usd")
            or float(os.environ.get("X402_MAX_PAYMENT_USD", "10.00")),
        "network": file_cfg.get("network")
            or os.environ.get("X402_NETWORK", "mainnet"),
    }

_config = _load_config()

# Base network RPC endpoints
BASE_RPC = {
    "mainnet": "https://mainnet.base.org",
    "testnet": "https://sepolia.base.org",
}

# USDC contract addresses on Base
USDC_ADDRESS = {
    "mainnet": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "testnet": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
}

# =============================================================================
# Wallet
# =============================================================================

def get_wallet():
    """Get wallet Account from EVM_PRIVATE_KEY (or WALLET_PRIVATE_KEY) env var."""
    key = os.environ.get("EVM_PRIVATE_KEY") or os.environ.get("WALLET_PRIVATE_KEY")
    if not key:
        print("Error: EVM_PRIVATE_KEY environment variable not set")
        print("Set your wallet private key: export EVM_PRIVATE_KEY=0x...")
        sys.exit(1)

    from eth_account import Account
    try:
        return Account.from_key(key)
    except Exception:
        print("Error: Invalid EVM_PRIVATE_KEY format (expected 0x + 64 hex chars)")
        sys.exit(1)


def _get_v2_header_transport():
    """
    Transport wrapper that injects the payment-required header for x402 V2
    providers that only put payment info in the response body (e.g., Kaito).

    The x402 SDK v2.1.0 has a bug: get_payment_required_response() only checks
    the payment-required header for V2 info. If a provider puts V2 payment info
    in the body only (no header), the SDK throws ValueError. This transport
    detects that case and injects the header from the body before the SDK sees it.
    """
    import httpx
    import base64

    class V2HeaderTransport(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request):
            response = await super().handle_async_request(request)
            if response.status_code != 402:
                return response

            # Check if payment-required header is already present
            if "payment-required" in response.headers:
                return response

            # Read body and check for V2 payment info
            content = await response.aread()
            try:
                body = json.loads(content.decode())
                if body.get("x402Version", 0) >= 2:
                    # Inject body as payment-required header (base64-encoded)
                    encoded = base64.b64encode(content).decode()
                    new_headers = httpx.Headers(response.headers)
                    new_headers["payment-required"] = encoded

                    from httpx._content import ByteStream
                    return httpx.Response(
                        status_code=response.status_code,
                        headers=new_headers,
                        stream=ByteStream(content),
                        extensions=response.extensions,
                    )
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

            # Return original with consumed content restored
            from httpx._content import ByteStream
            return httpx.Response(
                status_code=response.status_code,
                headers=response.headers,
                stream=ByteStream(content),
                extensions=response.extensions,
            )

    return V2HeaderTransport()


def _get_x402_httpx_client():
    """Create an x402-enabled httpx client using the v2 SDK."""
    from x402 import x402Client
    from x402.mechanisms.evm.signers import EthAccountSigner
    from x402.mechanisms.evm.exact.register import register_exact_evm_client
    from x402.http.x402_http_client import x402HTTPClient
    from x402.http.clients.httpx import x402AsyncTransport

    import httpx

    account = get_wallet()
    signer = EthAccountSigner(account)

    client = x402Client()
    register_exact_evm_client(client, signer)

    http_client = x402HTTPClient(client)

    # Chain transports: x402 payment transport wraps our V2 header fix transport
    # x402AsyncTransport -> V2HeaderTransport -> default httpx transport
    v2_transport = _get_v2_header_transport()
    payment_transport = x402AsyncTransport(http_client, v2_transport)

    return httpx.AsyncClient(transport=payment_transport, timeout=60)


# =============================================================================
# Core Functions (importable by other skills)
# =============================================================================

async def x402_fetch(url, method="GET", headers=None, body=None, max_usd=None):
    """
    Fetch a URL with automatic x402 payment handling.

    Tries a plain request first. If the server returns 402 Payment Required,
    initializes the x402 payment client and retries with payment.

    Args:
        url: The URL to fetch
        method: HTTP method (GET or POST)
        headers: Optional dict of extra headers
        body: Optional dict for POST body
        max_usd: Max payment in USD (default from config)

    Returns:
        Parsed JSON response (dict) or raw text (str)

    Raises:
        Exception on payment failure or HTTP error
    """
    import httpx

    req_headers = headers or {}

    # Try plain request first (works for free endpoints)
    async with httpx.AsyncClient(timeout=60) as client:
        if method.upper() == "GET":
            response = await client.get(url, headers=req_headers)
        else:
            response = await client.post(url, headers=req_headers, json=body)

    if response.status_code == 200:
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        return response.text

    # 402 — needs payment, initialize x402 client
    if response.status_code == 402:
        # Check max payment limit before paying
        max_payment = max_usd or _config["max_payment_usd"]
        try:
            payment_body = response.json()
            accepts = payment_body.get("accepts", [])
            if accepts:
                # maxAmountRequired is in USDC atomic units (6 decimals)
                max_amount_raw = int(accepts[0].get("maxAmountRequired", 0))
                max_amount_usd = max_amount_raw / 1e6
                if max_amount_usd > max_payment:
                    raise Exception(
                        f"Payment ${max_amount_usd:.2f} exceeds limit ${max_payment:.2f}. "
                        f"Use --max to increase."
                    )
        except (json.JSONDecodeError, KeyError, ValueError, IndexError):
            pass  # Can't parse payment info, let x402 SDK handle it

        # Use v2 SDK — handles 402 payment automatically
        httpx_client = _get_x402_httpx_client()

        if method.upper() == "GET":
            response = await httpx_client.get(url, headers=req_headers)
        else:
            response = await httpx_client.post(url, headers=req_headers, json=body)

        if response.status_code == 200:
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                return response.json()
            return response.text

        raise Exception(
            f"Payment sent but request failed: HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    raise Exception(f"HTTP {response.status_code}: {response.text[:500]}")


async def x402_balance():
    """
    Get wallet balances on Base.

    Returns:
        dict with address, usdc_balance, eth_balance, network
    """
    import httpx

    account = get_wallet()
    network = _config["network"]
    rpc_url = BASE_RPC[network]
    usdc_addr = USDC_ADDRESS[network]

    async with httpx.AsyncClient(timeout=15) as client:
        # ETH balance
        eth_resp = await client.post(rpc_url, json={
            "jsonrpc": "2.0", "id": 1, "method": "eth_getBalance",
            "params": [account.address, "latest"]
        })
        eth_raw = eth_resp.json().get("result", "0x0")
        eth_balance = int(eth_raw, 16) / 1e18

        # USDC balance (ERC20 balanceOf)
        # balanceOf(address) selector = 0x70a08231
        padded_addr = account.address[2:].lower().zfill(64)
        usdc_resp = await client.post(rpc_url, json={
            "jsonrpc": "2.0", "id": 2, "method": "eth_call",
            "params": [{"to": usdc_addr, "data": f"0x70a08231{padded_addr}"}, "latest"]
        })
        usdc_raw = usdc_resp.json().get("result", "0x0")
        usdc_balance = int(usdc_raw, 16) / 1e6  # USDC has 6 decimals

    return {
        "address": account.address,
        "usdc_balance": usdc_balance,
        "eth_balance": eth_balance,
        "network": "Base Mainnet" if network == "mainnet" else "Base Sepolia",
    }


# =============================================================================
# Quicknode x402 RPC
# =============================================================================

QUICKNODE_X402_BASE = "https://x402.quicknode.com"

async def _quicknode_auth():
    """Authenticate with Quicknode x402 via SIWE and return JWT."""
    import httpx
    import secrets
    from datetime import datetime, timezone
    from eth_account.messages import encode_defunct

    account = get_wallet()
    nonce = secrets.token_hex(8)
    issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    message = (
        f"x402.quicknode.com wants you to sign in with your Ethereum account:\n"
        f"{account.address}\n\n"
        f"By signing this message, you accept the Quicknode x402 Terms of Service.\n\n"
        f"URI: https://x402.quicknode.com\n"
        f"Version: 1\n"
        f"Chain ID: 8453\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued_at}"
    )

    signed = account.sign_message(encode_defunct(text=message))

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{QUICKNODE_X402_BASE}/auth", json={
            "message": message,
            "signature": signed.signature.hex() if isinstance(signed.signature, bytes) else hex(signed.signature),
        })
        if resp.status_code != 200:
            raise Exception(f"Quicknode auth failed: {resp.status_code} {resp.text[:200]}")
        return resp.json()["token"]


async def x402_rpc(network, method, params=None):
    """
    Make an RPC call via Quicknode x402.

    Authenticates with SIWE, then makes the RPC call. If credits run out,
    the x402 payment flow kicks in automatically.

    Args:
        network: Quicknode network name (e.g. 'ethereum-mainnet', 'polygon-mainnet')
        method: JSON-RPC method (e.g. 'eth_getBalance', 'eth_blockNumber')
        params: Optional list of RPC params

    Returns:
        The 'result' field from the JSON-RPC response
    """
    jwt = await _quicknode_auth()
    url = f"{QUICKNODE_X402_BASE}/{network}"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or [],
    }

    # Try with JWT auth first
    httpx_client = _get_x402_httpx_client()
    resp = await httpx_client.post(url, json=payload, headers={
        "Authorization": f"Bearer {jwt}",
        "Content-Type": "application/json",
    })

    if resp.status_code != 200:
        raise Exception(f"Quicknode RPC error: HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    if "error" in data:
        raise Exception(f"RPC error: {data['error']}")

    return data.get("result")


# =============================================================================
# CLI Commands
# =============================================================================

async def cmd_balance():
    """Show wallet balances."""
    bal = await x402_balance()
    print("x402 Wallet Balance")
    print("=" * 30)
    print(f"Address: {bal['address']}")
    print(f"Network: {bal['network']}")
    print()
    print(f"USDC:  ${bal['usdc_balance']:.2f}")
    print(f"ETH:   {bal['eth_balance']:.6f} ETH")


async def cmd_fetch(url, method="GET", headers=None, body=None,
                    output_json=False, dry_run=False, max_usd=None):
    """Fetch a URL with x402 payment."""
    if dry_run:
        print(f"[DRY RUN] Would fetch: {url}")
        print(f"  Method: {method}")
        print(f"  Max payment: ${max_usd or _config['max_payment_usd']:.2f}")
        print(f"  Network: Base {'Mainnet' if _config['network'] == 'mainnet' else 'Sepolia'}")
        print()
        print("To execute, remove --dry-run flag.")
        return

    try:
        data = await x402_fetch(url, method=method, headers=headers,
                                body=body, max_usd=max_usd)
        if isinstance(data, (dict, list)):
            print(json.dumps(data, indent=None if output_json else 2))
        else:
            print(data)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


# =============================================================================
# CLI Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Simmer x402 Payment Skill")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # balance
    subparsers.add_parser("balance", help="Check wallet balances on Base")

    # rpc
    rpc_parser = subparsers.add_parser("rpc", help="Make RPC call via Quicknode x402")
    rpc_parser.add_argument("network", help="Network name (e.g. ethereum-mainnet, polygon-mainnet, base-mainnet)")
    rpc_parser.add_argument("method", help="JSON-RPC method (e.g. eth_getBalance, eth_blockNumber)")
    rpc_parser.add_argument("params", nargs="*", help="RPC params (JSON strings or values)")

    # fetch
    fetch_parser = subparsers.add_parser("fetch", help="Fetch URL with x402 payment")
    fetch_parser.add_argument("url", help="URL to fetch")
    fetch_parser.add_argument("--method", default="GET", help="HTTP method (default: GET)")
    fetch_parser.add_argument("--header", action="append", metavar="KEY:VALUE",
                              help="Add header (can be used multiple times)")
    fetch_parser.add_argument("--body", help="JSON body for POST requests")
    fetch_parser.add_argument("--json", action="store_true", help="Output raw JSON")
    fetch_parser.add_argument("--dry-run", action="store_true", help="Preview without paying")
    fetch_parser.add_argument("--max", type=float, metavar="USD",
                              help=f"Max payment in USD (default: ${_config['max_payment_usd']:.2f})")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "balance":
        asyncio.run(cmd_balance())

    elif args.command == "rpc":
        # Parse params — try JSON parsing for each, fall back to string
        params = []
        for p in (args.params or []):
            try:
                params.append(json.loads(p))
            except (json.JSONDecodeError, ValueError):
                params.append(p)

        async def _run_rpc():
            try:
                result = await x402_rpc(args.network, args.method, params)
                if isinstance(result, (dict, list)):
                    print(json.dumps(result, indent=2))
                else:
                    print(result)
            except Exception as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)

        asyncio.run(_run_rpc())

    elif args.command == "fetch":
        # Parse headers
        headers = {}
        if args.header:
            for h in args.header:
                if ":" in h:
                    key, val = h.split(":", 1)
                    headers[key.strip()] = val.strip()

        # Parse body
        body = None
        if args.body:
            try:
                body = json.loads(args.body)
            except json.JSONDecodeError:
                print(f"Error: --body must be valid JSON", file=sys.stderr)
                sys.exit(1)

        asyncio.run(cmd_fetch(
            url=args.url,
            method=args.method,
            headers=headers if headers else None,
            body=body,
            output_json=args.json,
            dry_run=args.dry_run,
            max_usd=args.max,
        ))


if __name__ == "__main__":
    main()
