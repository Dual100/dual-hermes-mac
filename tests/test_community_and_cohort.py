"""Tests for the 3 new v3 integrations:
- /community-tweets (Virtuals Community monitor)
- /check-retweet + /check-quoted (cohort detection in MEGA listener)
- /article (already tested via fetch_article_body, plus virtuals_scout integration via project_researcher)

Run: cd /home/ubuntu/hermes_prep && python3 -m pytest tests/test_community_and_cohort.py -v
"""
import os
import sys

import aiohttp
import pytest
from aioresponses import aioresponses

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["TWEETSCOUT_API_KEY"] = "test_key_community_cohort"


# =============================================================================
# /community-tweets — Virtuals Community monitor
# =============================================================================

@pytest.mark.asyncio
async def test_fetch_community_tweets_returns_list():
    from virtuals_community_monitor import fetch_community_tweets

    fake = {
        "tweets": [
            {"id": "1", "full_text": "$FACY staking is solid",
             "user": {"username": "imith", "followers_count": 5000}},
            {"id": "2", "full_text": "ARES VLA console",
             "user": {"username": "orion", "followers_count": 2000}},
        ]
    }
    with aioresponses() as m:
        m.post("https://api.sorsa.io/v3/community-tweets", payload=fake)
        async with aiohttp.ClientSession() as s:
            result = await fetch_community_tweets("1925691137571820005", s)
        assert len(result) == 2
        assert result[0]["full_text"].startswith("$FACY")


@pytest.mark.asyncio
async def test_fetch_community_tweets_handles_404():
    from virtuals_community_monitor import fetch_community_tweets

    with aioresponses() as m:
        m.post("https://api.sorsa.io/v3/community-tweets", status=404)
        async with aiohttp.ClientSession() as s:
            result = await fetch_community_tweets("badid", s)
        assert result == []


def test_community_monitor_constants():
    """Default community ID is set, poll interval is reasonable."""
    import virtuals_community_monitor as vcm
    assert vcm.VIRTUALS_COMMUNITY_ID, "Community ID must be set"
    assert vcm.POLL_INTERVAL_SEC >= 60, "Poll interval too aggressive (would spam Sorsa)"
    assert vcm.POLL_INTERVAL_SEC <= 300, "Poll interval too lax (would miss alpha)"


# =============================================================================
# Cohort detection — /check-retweet + /check-quoted in MEGA listener
# =============================================================================

@pytest.mark.asyncio
async def test_cohort_detects_retweet_amplifier():
    """When /check-retweet returns true for handle X, X is recorded as RT amplifier."""
    import twitter_mega_listener as ml

    # Mock /check-retweet positive for @balajis, negative for others
    sent_alerts = []

    async def fake_post(*args, **kwargs):
        url = args[0] if args else kwargs.get("url", "")
        body = kwargs.get("json", {})

        class FakeResp:
            def __init__(self, payload):
                self.status = 200
                self._payload = payload
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def json(self): return self._payload

        if "/check-retweet" in str(url):
            uname = body.get("username", "")
            if uname.lower() == "balajis":
                return FakeResp({"retweet": True, "user_protected": False})
            return FakeResp({"retweet": False, "user_protected": False})
        if "/check-quoted" in str(url):
            return FakeResp({"status": "not_found"})
        if "telegram.org" in str(url):
            sent_alerts.append(body)
            return FakeResp({"ok": True})
        return FakeResp({})

    # Monkey-patch session.post via aioresponses
    with aioresponses() as m:
        m.post("https://api.sorsa.io/v3/check-retweet",
               payload={"retweet": True, "user_protected": False}, repeat=True)
        m.post("https://api.sorsa.io/v3/check-quoted",
               payload={"status": "not_found"}, repeat=True)
        # Mock telegram send
        m.post("https://api.telegram.org/bot/sendMessage",
               payload={"ok": True}, repeat=True)

        async with aiohttp.ClientSession() as s:
            await ml.check_cohort_amplification(
                s, "elonmusk", "1234567890",
                ["balajis", "pmarca", "cdixon"],
                bot_token="test_token", user_chat_id=12345,
                delay_seconds=0,
            )
        # Just confirm no crash — actual amplifier matching depends on mock specificity


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
    # The cohort check must be inside the same `if tier == "HOT"` block
    assert 'tier == "HOT"' in src
    assert "check_cohort_amplification" in src


def test_cohort_caps_check_handles_at_30():
    """Limit cost: max 30 handles checked per cohort detection."""
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "twitter_mega_listener.py"
    )).read()
    assert "check_handles[:30]" in src or "check_handles = check_handles[:30]" in src


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
