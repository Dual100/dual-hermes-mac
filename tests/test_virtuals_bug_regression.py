"""Regression tests for the 🎭 Virtuals false positive bug.

THE BUG: /virtuals/token endpoint returns {address, found, token}.
The code was checking `data.get("address")` which always echoed the input,
so EVERY token was being marked as is_virtuals=True. This caused phantom
+30 ELITE Virtuals trust_weight bonuses on $MLM and $PNKSTR (Ethereum tokens
that have nothing to do with Virtuals).

THE FIX:
  - Check `data.get("found") and tok` (found = real DB hit)
  - Exclude factory == "clanker" (same DB has 509K Clanker tokens mixed in)
  - Use `tok` (the inner row) for fields, NOT `data` (the wrapper)

Run: cd /home/ubuntu/hermes_prep && python3 -m pytest tests/test_virtuals_bug_regression.py -v
"""
import os
import sys
from unittest.mock import patch

import aiohttp
import pytest
from aioresponses import aioresponses

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force fake env so the network is mocked
os.environ.setdefault("HERMES_DATA_API_URL", "https://fake.test/hermes")
os.environ.setdefault("HERMES_DATA_API_KEY", "fake")


@pytest.mark.asyncio
async def test_non_virtuals_token_is_not_marked_as_virtuals():
    """The bug: $MLM ETH (address not in DB) was being marked is_virtuals=True
    because data.get('address') always returned the echoed input.

    Fix: check `found` field instead.
    """
    from telegram_group_listener import _virtuals_deep_check

    with aioresponses() as m:
        # Endpoint echoes the input address but reports found=False
        m.get(
            "https://fake.test/hermes/virtuals/token?address=0xnotvirtuals",
            payload={"address": "0xnotvirtuals", "found": False, "token": None},
        )
        async with aiohttp.ClientSession() as s:
            result = await _virtuals_deep_check("0xnotvirtuals", s)

        assert result == {}, (
            f"Non-Virtuals token must return empty dict (no is_virtuals flag). "
            f"Got: {result}"
        )


@pytest.mark.asyncio
async def test_clanker_token_is_not_marked_as_virtuals():
    """Bonus bug: virtuals_all_tokens.db contains 509K Clanker tokens.
    A Clanker token gets `found=True` from the lookup but is NOT a Virtuals
    platform token. Must filter by factory."""
    from telegram_group_listener import _virtuals_deep_check

    with aioresponses() as m:
        m.get(
            "https://fake.test/hermes/virtuals/token?address=0xclanker",
            payload={
                "address": "0xclanker",
                "found": True,
                "token": {
                    "token_address": "0xclanker",
                    "symbol": "TEST",
                    "factory": "clanker",  # ← Clanker, not Virtuals
                    "creator_wallet": "0xcreator",
                },
            },
        )
        async with aiohttp.ClientSession() as s:
            result = await _virtuals_deep_check("0xclanker", s)

        assert result == {}, (
            f"Clanker token must NOT be marked is_virtuals. Got: {result}"
        )


@pytest.mark.asyncio
async def test_real_virtuals_token_is_marked_correctly():
    """Positive case: real BONDING_V4 (Virtuals platform) token is correctly flagged."""
    from telegram_group_listener import _virtuals_deep_check

    real_virtuals_token = {
        "token_address": "0xrealvirt",
        "symbol": "AGENT",
        "factory": "BONDING_V4",
        "creator_wallet": "0xcreator",
        "creator_twitter": "real_creator",
        "holder_count": 250,
    }
    with aioresponses() as m:
        m.get(
            "https://fake.test/hermes/virtuals/token?address=0xrealvirt",
            payload={"address": "0xrealvirt", "found": True, "token": real_virtuals_token},
        )
        # The function makes additional calls; mock them as 404 to short-circuit
        m.get(
            "https://fake.test/hermes/creators/is-farmer?wallet=0xcreator",
            status=404, repeat=True,
        )
        m.get(
            "https://fake.test/hermes/investigations/developer?wallet=0xcreator",
            status=404, repeat=True,
        )
        m.get(
            "https://fake.test/hermes/creators/by-wallet?wallet=0xcreator",
            status=404, repeat=True,
        )
        async with aiohttp.ClientSession() as s:
            result = await _virtuals_deep_check("0xrealvirt", s)

        assert result.get("is_virtuals") is True
        assert result.get("creator_wallet") == "0xcreator"
        assert result.get("twitter") == "real_creator"


@pytest.mark.asyncio
async def test_batch_endpoint_consumer_filters_clanker():
    """The other bug location: batch_hermes consumer also needs to filter.

    The batch endpoint returns the raw DB row in `virtuals` field. If the row
    has factory='clanker', the consumer must NOT set is_virtuals=True.

    This is checked by the inline logic in run_listener after _batch_hermes_data.
    """
    # Read the source to confirm the filter is in place — fast static check
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "telegram_group_listener.py"
    )).read()

    # The fix line:
    assert 'factory_b != "clanker"' in src, (
        "Missing Clanker filter in batch consumer! Bug regression risk."
    )
    # Must check token_address (DB field), NOT address (which is never in DB rows):
    assert 'v.get("token_address")' in src, (
        "Batch consumer must check token_address (DB field), not address."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
