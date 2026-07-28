"""Nonce allocation (SIM-4223). No network, no [hyperliquid] extra needed.

A bare wall-clock millisecond is not a safe nonce: HL tracks nonces per signing
key and accepts each once, so two actions signed by one key inside the same
millisecond collide and only one lands — a paired buy/sell can lose a leg.
"""

import threading

from simmer_sdk.hyperliquid_signing import next_nonce, now_ms

ADDR_A = "0x" + "aa" * 20
ADDR_B = "0x" + "bb" * 20


def test_rapid_sequential_calls_never_repeat():
    """The actual bug: sequential calls land in one millisecond on a fast host.
    A bare now_ms() returns duplicates here."""
    nonces = [next_nonce(ADDR_A) for _ in range(2000)]
    assert len(set(nonces)) == len(nonces), "duplicate nonce issued"
    assert nonces == sorted(nonces), "nonces not monotonic"


def test_bare_timestamp_would_have_collided():
    """Guards the premise: if now_ms() didn't repeat under this loop, the test
    above could pass while proving nothing."""
    stamps = [now_ms() for _ in range(2000)]
    assert len(set(stamps)) < len(stamps), (
        "now_ms() did not repeat — this machine is too slow for the test to "
        "discriminate; the allocator test above is not meaningful here"
    )


def test_addresses_are_tracked_independently():
    """Keyed per address so a busy key cannot skew an idle key's nonces
    forward, matching HL's own per-key accounting."""
    burst = [next_nonce(ADDR_A) for _ in range(500)]
    fresh = next_nonce(ADDR_B)
    assert fresh <= max(burst), "idle address inherited a busy address's skew"
    assert fresh >= now_ms() - 1000


def test_address_matching_is_case_insensitive():
    """Checksummed vs lowercase spellings of one address are one key."""
    a = next_nonce("0x" + "CD" * 20)
    b = next_nonce("0x" + "cd" * 20)
    assert b > a


def test_concurrent_allocation_is_unique():
    """Thread-safe: the lock is what makes read-modify-write atomic."""
    out = []
    lock = threading.Lock()

    def worker():
        got = [next_nonce(ADDR_A) for _ in range(200)]
        with lock:
            out.extend(got)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(out)) == len(out), "concurrent callers got a duplicate nonce"


def test_nonce_tracks_wall_clock():
    """Must stay in HL's accepted time window, not drift into the future."""
    n = next_nonce("0x" + "ef" * 20)
    assert abs(n - now_ms()) < 5000
