"""
X (Twitter) Browser Search — Playwright-based, login with alt account.

Runs a headless Chrome on the Mac, logged in with a DEDICATED Twitter account
(not your main one!). Searches x.com just like a human would, parses results
from DOM.

WHY this beats Brave/Nitter:
- Rate limit = human level (much higher than API limits)
- Fresh results (real-time, not indexed delayed)
- No free-tier quotas
- Works for any query

DEDICATED ACCOUNT NEEDED:
- Create a separate @something_alt account on X
- Do NOT use your main account (risk of ban)
- Keep it logged in via persistent browser profile
- Recommend 3 alt accounts for rotation

ANTI-BAN STRATEGIES:
- Random delays 2-8s between actions
- Realistic user agent
- Human-like scrolling
- Rotate accounts on rate limit signs
- Session persistence (don't re-login constantly)
- Run from Mac Mini (real IP, not datacenter)

Install:
    pip install playwright
    playwright install chromium
"""

import asyncio
import logging
import os
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
except ImportError:
    async_playwright = None

logger = logging.getLogger("x_browser_search")

# =============================================================================
# CONFIG
# =============================================================================

# Persistent browser profiles per account (cookies stay saved)
PROFILES_DIR = Path.home() / "hermes-mac" / "data" / "x_profiles"
PROFILES_DIR.mkdir(parents=True, exist_ok=True)

# User agents (rotated per session)
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]

HEADLESS = True  # set False to watch it work (debugging)


# =============================================================================
# AccountPool — rotate across alt accounts
# =============================================================================

class XAccount:
    def __init__(self, handle: str, password: Optional[str] = None):
        self.handle = handle
        self.password = password
        self.profile_dir = PROFILES_DIR / handle
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.rate_limited_until: float = 0


class AccountPool:
    """Manages multiple X accounts with round-robin + rate-limit awareness."""

    def __init__(self, accounts: List[XAccount]):
        self.accounts = accounts
        self._index = 0

    def pick(self) -> Optional[XAccount]:
        """Pick next available account (not rate-limited)."""
        import time
        now = time.time()
        for _ in range(len(self.accounts)):
            acc = self.accounts[self._index % len(self.accounts)]
            self._index += 1
            if acc.rate_limited_until <= now:
                return acc
        return None  # all rate-limited

    def mark_rate_limited(self, handle: str, cooldown_seconds: int = 900):
        import time
        for acc in self.accounts:
            if acc.handle == handle:
                acc.rate_limited_until = time.time() + cooldown_seconds
                logger.warning(f"Account {handle} rate-limited for {cooldown_seconds}s")


# =============================================================================
# Browser search
# =============================================================================

_browser: Optional[Browser] = None


async def _get_browser():
    global _browser
    if _browser is not None:
        return _browser
    if async_playwright is None:
        raise RuntimeError("playwright not installed: pip install playwright && playwright install chromium")
    pw = await async_playwright().start()
    _browser = await pw.chromium.launch(
        headless=HEADLESS,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )
    return _browser


async def _get_context(account: XAccount) -> BrowserContext:
    browser = await _get_browser()
    # Persistent context keeps cookies across runs
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1280, "height": 800},
        locale="en-US",
        timezone_id="America/Sao_Paulo",
        storage_state=str(account.profile_dir / "state.json") if (account.profile_dir / "state.json").exists() else None,
    )
    # Mask webdriver property
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', { get: () => undefined })"
    )
    return context


async def _save_state(account: XAccount, context: BrowserContext):
    """Save cookies for next session."""
    try:
        await context.storage_state(path=str(account.profile_dir / "state.json"))
    except Exception as e:
        logger.warning(f"Failed to save state for {account.handle}: {e}")


async def _check_logged_in(page: Page) -> bool:
    """Quick check: are we still logged in?"""
    try:
        await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=15000)
        # Logged-in indicator: sidebar navigation or compose tweet button
        logged = await page.query_selector('a[data-testid="AppTabBar_Home_Link"]')
        return logged is not None
    except Exception:
        return False


async def _human_delay(min_s: float = 1.0, max_s: float = 3.0):
    await asyncio.sleep(random.uniform(min_s, max_s))


async def _search_with_account(
    query: str,
    account: XAccount,
    pool: AccountPool,
    count: int = 20,
) -> Dict[str, Any]:
    """Do a single search using one account."""
    context = await _get_context(account)
    page = await context.new_page()
    try:
        if not await _check_logged_in(page):
            return {
                "error": "not_logged_in",
                "detail": f"Account {account.handle} session expired — needs manual login",
            }

        # Go to search results
        from urllib.parse import quote
        search_url = f"https://x.com/search?q={quote(query)}&src=typed_query&f=live"
        await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
        await _human_delay(2, 4)

        # Detect rate limit page
        page_text = await page.content()
        if "rate limit" in page_text.lower() or "try again later" in page_text.lower():
            pool.mark_rate_limited(account.handle)
            return {"error": "rate_limited"}

        # Scroll a bit to load more tweets
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await _human_delay(1.5, 3)

        # Extract tweets from DOM
        mentions = await page.evaluate(
            """() => {
                const tweets = document.querySelectorAll('article[data-testid="tweet"]');
                const results = [];
                tweets.forEach(t => {
                    try {
                        const userLink = t.querySelector('a[href*="/status/"]');
                        if (!userLink) return;
                        const href = userLink.getAttribute('href');
                        const match = href.match(/\\/([^/]+)\\/status\\/(\\d+)/);
                        if (!match) return;
                        const author = match[1];
                        const tweet_id = match[2];
                        const content = (t.querySelector('[data-testid="tweetText"]')?.innerText || '').slice(0, 500);
                        const time = t.querySelector('time')?.getAttribute('datetime') || null;
                        // Engagement numbers (approx)
                        const likes = parseInt(
                            (t.querySelector('[data-testid="like"]')?.innerText || '0').replace(/[^\\d]/g, '') || '0'
                        );
                        const rts = parseInt(
                            (t.querySelector('[data-testid="retweet"]')?.innerText || '0').replace(/[^\\d]/g, '') || '0'
                        );
                        results.push({
                            tweet_id, author, content,
                            posted_at: time,
                            engagement: likes + rts,
                            url: 'https://x.com' + href,
                        });
                    } catch(e) {}
                });
                return results;
            }"""
        )

        await _save_state(account, context)

        return {
            "query": query,
            "source": f"browser_{account.handle}",
            "mentions": mentions[:count],
            "total_found": len(mentions),
        }
    except Exception as e:
        logger.exception(f"Browser search failed: {e}")
        return {"error": "browser_exception", "detail": str(e)}
    finally:
        await page.close()
        await context.close()


async def search_x_browser(
    query: str,
    pool: AccountPool,
    count: int = 20,
) -> Dict[str, Any]:
    """Public API: search X via browser, with account rotation."""
    for attempt in range(len(pool.accounts)):
        account = pool.pick()
        if account is None:
            return {"error": "all_accounts_rate_limited"}
        result = await _search_with_account(query, account, pool, count)
        if "error" not in result:
            return result
        if result.get("error") == "rate_limited":
            continue  # try next account
        return result  # hard failure (not_logged_in etc)
    return {"error": "exhausted_accounts"}


# =============================================================================
# FIRST-RUN LOGIN (interactive, once per account)
# =============================================================================

async def login_account_interactive(handle: str, password: str):
    """
    Run ONCE to login an account. Not headless — you'll see the browser.
    Saves cookies for future headless use.
    """
    account = XAccount(handle, password)
    context_browser = await async_playwright().start()
    browser = await context_browser.chromium.launch(headless=False)
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1280, "height": 800},
    )
    page = await context.new_page()

    await page.goto("https://x.com/login")
    print(f"\nInterface opened. Login manually as {handle} (handle 2FA if needed).")
    print("When you see your home feed, return to this terminal.")
    input("Press ENTER once logged in successfully...")

    # Save state
    await context.storage_state(path=str(account.profile_dir / "state.json"))
    print(f"✅ State saved for {handle}. You can now use search_x_browser.")

    await context.close()
    await browser.close()
    await context_browser.stop()
