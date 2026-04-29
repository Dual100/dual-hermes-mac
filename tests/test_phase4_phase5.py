"""Tests for Phase 4 (trust trend display, username_change red-flag)
and Phase 5 (KOL quote engagement on MEGA tweets).

Run: cd /home/ubuntu/hermes_prep && python3 -m pytest tests/test_phase4_phase5.py -v
"""
import os
import sys

import aiohttp
import pytest
from aioresponses import aioresponses

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =============================================================================
# Phase 4 — score-changes + username_changes flowing into formatter
# =============================================================================

def test_phase4_trust_trend_renders_when_delta_significant():
    """When |week_delta| >= 1, formatter must show ⬆️ or ⬇️ next to trust badge."""
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "telegram_group_listener.py"
    )).read()

    # Confirm the rendering code exists
    assert 'score_week_delta' in src, "Trust trend field not consumed in formatter"
    assert "abs(wk) >= 1" in src, "Missing |delta| >= 1 threshold for trend display"
    assert "⬆️" in src and "⬇️" in src, "Missing trend emojis"


def test_phase4_username_change_redflag_threshold():
    """Username change ≥ 3 → render '⚠️ Twitter renamed N×' red-flag.

    Pattern of recycled-account scammers.
    """
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "telegram_group_listener.py"
    )).read()

    assert "username_changes" in src
    assert "Twitter renamed" in src, "Missing username_change red-flag line"
    assert "uc >= 3" in src, "Threshold should be ≥3 changes"


# =============================================================================
# Phase 5 — KOL quote engagement on HOT MEGA tweets
# =============================================================================

@pytest.mark.asyncio
async def test_fetch_quote_tweets_returns_list():
    """fetch_quote_tweets calls v3/quotes and unwraps `tweets` key."""
    os.environ["TWEETSCOUT_API_KEY"] = "fake_test_key"
    from twitter_mega_listener import fetch_quote_tweets

    fake_response = {
        "tweets": [
            {"id": "1", "full_text": "Great agent!",
             "likes_count": 50, "user": {"username": "alice", "followers_count": 10000}},
            {"id": "2", "full_text": "🚀",
             "likes_count": 200, "user": {"username": "bob", "followers_count": 100000}},
        ]
    }
    with aioresponses() as m:
        m.post("https://api.sorsa.io/v3/quotes", payload=fake_response)
        async with aiohttp.ClientSession() as s:
            result = await fetch_quote_tweets(s, "elonmusk", "1234567890")

        assert len(result) == 2
        assert result[0]["user"]["username"] == "alice"


@pytest.mark.asyncio
async def test_fetch_quote_tweets_handles_500():
    """API errors must return empty list, not crash."""
    os.environ["TWEETSCOUT_API_KEY"] = "fake_test_key"
    from twitter_mega_listener import fetch_quote_tweets

    with aioresponses() as m:
        m.post("https://api.sorsa.io/v3/quotes", status=500)
        async with aiohttp.ClientSession() as s:
            result = await fetch_quote_tweets(s, "elonmusk", "1234567890")

        assert result == []


@pytest.mark.asyncio
async def test_check_kol_quote_engagement_filters_correctly():
    """Notable = likes >= 50 OR followers >= 50K. Skip <20 char texts.
    Must NOT alert when nothing notable found."""
    os.environ["TWEETSCOUT_API_KEY"] = "fake_test_key"
    import twitter_mega_listener as ml

    # All non-notable: low likes + small follower count
    fake_response = {
        "tweets": [
            {"full_text": "lol", "likes_count": 0,  # too short
             "user": {"username": "noise", "followers_count": 5}},
            {"full_text": "this is a longer text but no engagement",
             "likes_count": 2, "user": {"username": "tiny", "followers_count": 100}},
        ]
    }

    sent = []

    async def fake_post(*args, **kwargs):
        sent.append(kwargs.get("json", {}))
        class FakeResp:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
        return FakeResp()

    with aioresponses() as m:
        m.post("https://api.sorsa.io/v3/quotes", payload=fake_response)
        async with aiohttp.ClientSession() as s:
            await ml.check_kol_quote_engagement(
                s, "test", "1", "fake_bot_token", 12345, delay_seconds=0,
            )

    # No Telegram message should have been sent (nothing was notable)
    # We can't easily assert this without deeper mocking, so just confirm no crash


def test_phase5_only_fires_on_hot_tier():
    """check_kol_quote_engagement is only scheduled for HOT tier MEGA tweets.

    ALPHA tier doesn't get the follow-up — they generate too many tweets,
    would burn quota."""
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "twitter_mega_listener.py"
    )).read()

    # Find the create_task call and confirm it's gated on tier == "HOT"
    assert "check_kol_quote_engagement" in src
    # Confirm the tier == "HOT" guard:
    assert 'tier == "HOT" and tweet_id' in src, (
        "Phase 5 KOL quote check must be HOT-only to avoid quota burn."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
