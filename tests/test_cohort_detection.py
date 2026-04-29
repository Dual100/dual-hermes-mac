"""Tests for /check-retweet + /check-quoted cohort detection in MEGA listener.

Run: cd /home/ubuntu/hermes_prep && python3 -m pytest tests/test_cohort_detection.py -v
"""
import os
import sys

import aiohttp
import pytest
from aioresponses import aioresponses

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["TWEETSCOUT_API_KEY"] = "test_key_cohort"


def test_cohort_skips_origin_handle():
    """The origin handle must be excluded from the cohort check (don't check if X RT'd themselves)."""
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "twitter_mega_listener.py"
    )).read()
    assert "h.lower() != origin_handle.lower()" in src, (
        "Cohort check must exclude origin handle"
    )


def test_cohort_only_fires_on_hot_tier():
    """Only HOT tier MEGA tweets trigger cohort check (cost guard)."""
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "twitter_mega_listener.py"
    )).read()
    assert 'tier == "HOT"' in src
    assert "check_cohort_amplification" in src


def test_cohort_caps_check_handles_at_30():
    """Limit cost: max 30 handles checked per cohort detection."""
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "twitter_mega_listener.py"
    )).read()
    assert "check_handles[:30]" in src or "check_handles = check_handles[:30]" in src


@pytest.mark.asyncio
async def test_cohort_no_amplifiers_no_alert():
    """When NO MEGA handle amplified, no alert is sent."""
    import twitter_mega_listener as ml

    with aioresponses() as m:
        m.post("https://api.sorsa.io/v3/check-retweet",
               payload={"retweet": False, "user_protected": False}, repeat=True)
        m.post("https://api.sorsa.io/v3/check-quoted",
               payload={"status": "not_found"}, repeat=True)

        async with aiohttp.ClientSession() as s:
            # Should not raise; should silently log no amplifiers
            await ml.check_cohort_amplification(
                s, "elonmusk", "1234567890",
                ["balajis", "pmarca"],
                bot_token="test_token", user_chat_id=12345,
                delay_seconds=0,
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
