"""Submits posted clips to their Content Rewards campaign with a headless browser.

Content Rewards has no submission API and only accepts links posted in the last 30 minutes,
so this runs right after a clip goes public. It uses a saved login (cookies) made once on
your Mac with `clipmill cr-login`; nothing here types passwords or codes.
The form: a "Clip link" URL box, a requirements checkbox, and a "Submit clip" button.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("clipmill")
BASE = "https://contentrewards.com"


class SessionExpired(RuntimeError):
    pass


def state_from_env_or_file(path: Path) -> dict | None:
    raw = os.environ.get("CR_STATE_JSON")
    if raw:
        return json.loads(raw)
    if path.exists():
        return json.loads(path.read_text())
    return None


def login(state_path: Path, timeout_s: int = 600) -> None:
    """Opens a real browser window on your Mac. You sign in to Content Rewards (email code or
    Google, as momentsg45). Once the dashboard loads, the login is saved to state_path."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(f"{BASE}/login?role=creator")
        print("Sign in as GTA 6 Moments in the window that opened. Waiting...")
        page.wait_for_url(lambda u: "/c/" in u, timeout=timeout_s * 1000)
        page.wait_for_timeout(2000)
        state_path.write_text(json.dumps(ctx.storage_state()))
        browser.close()


def submit(campaign_url: str, post_url: str, state: dict, shots_dir: Path | None = None,
           timeout_ms: int = 30000) -> str:
    """Returns a short status string. Raises SessionExpired if the saved login stopped working."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.environ.get("CLIPMILL_CHROME") or None)
        ctx = browser.new_context(storage_state=state, viewport={"width": 600, "height": 1000})
        page = ctx.new_page()
        try:
            page.goto(campaign_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(2500)
            if "/login" in page.url:
                raise SessionExpired("Content Rewards login expired; run `clipmill cr-login` on your Mac")
            page.get_by_role("button", name="Submit clip").first.click(timeout=timeout_ms)
            link = page.locator('input[aria-label="Clip link"]')
            link.wait_for(state="visible", timeout=timeout_ms)
            link.fill(post_url)
            page.wait_for_timeout(1500)  # the form checks the link against your connected accounts
            dialog_root = link.locator("xpath=ancestor::*[.//input[@type='checkbox']][1]")
            box = dialog_root.locator('input[type="checkbox"]').first
            if not box.is_checked():
                box.check(force=True)
            dialog_root.get_by_role("button", name="Submit clip").last.click(timeout=timeout_ms)
            page.wait_for_timeout(4000)
            text = page.locator("body").inner_text()
            for bad in ("already been submitted", "older than", "not connected", "Invalid", "couldn't", "error"):
                if bad.lower() in text.lower():
                    raise RuntimeError(f"Content Rewards said: ...{bad}...")
            return "submitted"
        except Exception:
            if shots_dir:
                shots_dir.mkdir(parents=True, exist_ok=True)
                try:
                    page.screenshot(path=str(shots_dir / "cr_submit_failure.png"), full_page=True)
                except Exception:
                    pass
            raise
        finally:
            browser.close()
