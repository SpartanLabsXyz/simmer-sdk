"""The server's empty-search reason must reach the caller (simmer#2292, SIM-5170).

`GET /api/sdk/markets` returns `empty_reason` (`filtered_untradeable` |
`filtered_expired` | `no_matches`) with a `hint` and, where relevant,
`matched_before_tradeable_filter`. The client dropped all of it: `get_markets`
returns a plain list at the default `response_mode`, so the envelope went
nowhere and an agent got a bare `[]`. It also had no `tradeable_only`
parameter, which is the retry the `filtered_untradeable` hint asks for -- so
the hint named something no caller could do.

Grok Bot dogfood hit exactly this: `find_markets("temperature")` returned 0
while bitcoin still hit, with no way to tell a catalog miss from weather
markets going stale as a cohort.
"""

from unittest.mock import MagicMock

from simmer_sdk.client import MarketList, SimmerClient


def _client() -> SimmerClient:
    return SimmerClient.__new__(SimmerClient)


def _row(market_id: str = "m1"):
    return {
        "id": market_id,
        "question": "Highest temperature in NYC?",
        "status": "active",
        "current_probability": 0.4,
    }


def _empty_filtered(matched: int = 12):
    return {
        "markets": [],
        "total": 0,
        "empty_reason": "filtered_untradeable",
        "matched_before_tradeable_filter": matched,
        "hint": (
            f"{matched} matching market(s) were excluded because tradeable_only "
            f"defaults true. Retry with tradeable_only=false, or retry shortly."
        ),
    }


# --- tradeable_only reaches the wire -----------------------------------------


def test_default_does_not_send_tradeable_only():
    """Existing callers keep their exact query string."""
    client = _client()
    client._request = MagicMock(return_value={"markets": [], "total": 0})

    client.get_markets(q="temperature")

    params = client._request.call_args.kwargs["params"]
    assert "tradeable_only" not in params


def test_tradeable_only_false_is_sent():
    """The retry the server's hint asks for is actually expressible."""
    client = _client()
    client._request = MagicMock(return_value={"markets": [_row()], "total": 1})

    client.get_markets(q="temperature", tradeable_only=False)

    params = client._request.call_args.kwargs["params"]
    assert params["tradeable_only"] == "false"


# --- the reason survives to the caller ---------------------------------------


def test_empty_result_carries_the_server_reason():
    client = _client()
    client._request = MagicMock(return_value=_empty_filtered())

    result = client.get_markets(q="temperature")

    assert result == []
    assert result.empty_reason == "filtered_untradeable"
    assert result.matched_before_tradeable_filter == 12
    assert "tradeable_only=false" in result.hint


def test_repr_of_empty_result_shows_the_reason():
    """The discoverability property: an agent that prints the result sees why.

    An attribute nobody thinks to read is the same as no attribute at all --
    this is the assertion that makes the metadata reachable in practice.
    """
    client = _client()
    client._request = MagicMock(return_value=_empty_filtered())

    printed = repr(client.get_markets(q="temperature"))

    assert "empty_reason=filtered_untradeable" in printed
    assert "matched_before_tradeable_filter=12" in printed
    assert "tradeable_only=false" in printed


def test_non_empty_result_has_no_reason_and_a_plain_repr():
    client = _client()
    client._request = MagicMock(return_value={"markets": [_row()], "total": 1})

    result = client.get_markets(q="temperature")

    assert len(result) == 1
    assert result.empty_reason is None
    assert repr(result).startswith("[Market(")
    assert "empty_reason" not in repr(result)


def test_pagination_hint_is_not_mistaken_for_an_empty_reason():
    """`hint` is overloaded server-side -- pagination and capped-window use it too.

    Reading it without `empty_reason` present would attach "More results
    available. Use offset=50" to a result as though it explained an emptiness.
    """
    client = _client()
    client._request = MagicMock(return_value={
        "markets": [_row()],
        "total": 200,
        "truncated": True,
        "hint": "More results available. Use offset=50 to get the next page.",
    })

    result = client.get_markets(q="temperature")

    assert result.empty_reason is None
    assert result.hint is None


# --- nothing existing breaks -------------------------------------------------


def test_return_is_still_a_list_in_every_respect():
    client = _client()
    client._request = MagicMock(
        return_value={"markets": [_row("m1"), _row("m2")], "total": 2}
    )

    result = client.get_markets()

    assert isinstance(result, list)
    assert isinstance(result, MarketList)
    assert len(result) == 2
    assert result[0].id == "m1"
    assert [m.id for m in result] == ["m1", "m2"]
    assert isinstance(result[0:1], list)
    assert result != []


def test_empty_result_without_a_reason_is_a_bare_empty_list():
    """Servers that predate the field (or a replay server) must stay unchanged."""
    client = _client()
    client._request = MagicMock(return_value={"markets": [], "total": 0})

    result = client.get_markets(q="temperature")

    assert result == []
    assert result.empty_reason is None
    assert repr(result) == "[]"


# --- find_markets, the path that filed the bug -------------------------------


def test_find_markets_propagates_the_reason():
    client = _client()
    client._request = MagicMock(return_value=_empty_filtered(matched=7))

    result = client.find_markets("temperature")

    assert result == []
    assert result.empty_reason == "filtered_untradeable"
    assert result.matched_before_tradeable_filter == 7


# --- compact mode uses the server's sentence, not the hardcoded one ----------


def test_compact_empty_message_uses_the_server_reason():
    """`"No markets matched your filters."` is the wrong sentence when they did match."""
    client = _client()
    client._request = MagicMock(return_value=_empty_filtered())

    envelope = client.get_markets(q="temperature", response_mode="summary")

    assert envelope["count"] == 0
    assert "tradeable_only=false" in envelope["message"]
    assert envelope["message"] != "No markets matched your filters."


def test_compact_empty_message_falls_back_when_server_gives_no_reason():
    client = _client()
    client._request = MagicMock(return_value={"markets": [], "total": 0})

    envelope = client.get_markets(q="zzzz", response_mode="summary")

    assert envelope["message"] == "No markets matched your filters."
