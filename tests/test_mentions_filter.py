"""Test the /mentions vs /search-tweets routing for spam filtering.

Phase 2: ticker/name queries route to /mentions with min_likes=5
(cuts ~75% of bot spam). Address/dev queries keep /search-tweets
(every organic mention matters even at 0 likes).

Run: cd /home/ubuntu/hermes_prep && python3 -m pytest tests/test_mentions_filter.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_search_routing_logic_in_source():
    """Static guarantee: the routing logic is in the file.

    If someone refactors and drops the label-aware routing, this fails.
    """
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "telegram_group_listener.py"
    )).read()

    # The condition that routes to /mentions:
    assert 'use_mentions = label in ("ticker", "name")' in src, (
        "Missing label-based routing for /mentions endpoint."
    )
    # The min_likes filter:
    assert '"min_likes"' in src and "5" in src, (
        "Missing min_likes=5 spam filter."
    )
    # Both endpoints still must exist:
    assert "/mentions" in src
    assert "/search-tweets" in src


def test_v2_search_tweets_fallback_was_removed():
    """Phase 1 cleanup: the dead v2 fallback in twitter_search.py must be gone.

    Was: search_sorsa_v2() called on 404 from v3.
    Now: removed entirely (v2 sunsets May 1 2026).
    """
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "twitter_search.py"
    )).read()

    assert "search_sorsa_v2" not in src, (
        "v2 fallback function must be removed. v2 sunsets May 1 2026."
    )
    assert "api.sorsa.io/v2" not in src, (
        "No more v2 URLs allowed in twitter_search.py."
    )


def test_check_follow_uses_v3_url_in_listener():
    """Hermes listener line ~523 must POST to v3/check-follow with v3 params."""
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "telegram_group_listener.py"
    )).read()

    # Confirm v3 URL is the only check-follow call:
    assert "api.sorsa.io/v3/check-follow" in src
    assert "api.sorsa.io/v2/check-follow" not in src
    # Confirm v3 params:
    assert '"username_1": "virtuals_io"' in src or "'username_1':" in src
    assert "project_handle" not in src or src.count("project_handle") <= 1, (
        "v2 param `project_handle` must not be used for Sorsa anymore."
    )


def test_cooldown_default_is_30min_or_more():
    """Cooldown was 5min (300s) — too short, allowed 25× repeat investigations
    of same address per day. Must be ≥1800s (30min)."""
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "telegram_group_listener.py"
    )).read()

    # Find the COOLDOWN_SEC default
    import re
    m = re.search(r'COOLDOWN_SEC\s*=\s*int\(os\.environ\.get\(["\']HERMES_TOKEN_COOLDOWN_SEC["\'],\s*["\'](\d+)["\']\)\)', src)
    assert m, "COOLDOWN_SEC line not found"
    default = int(m.group(1))
    assert default >= 1800, (
        f"Cooldown default is {default}s. Must be ≥1800s (30min) to avoid "
        f"~25× daily repeat investigations from ETH Volume Spike-style sources."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
