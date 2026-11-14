"""One-time manual login for the BonpreuEsclat Energia Oficina Virtual.

Opens a headed browser at the portal, waits until you have logged in (including any
one-time code / 2FA step), then saves the session to BONPREU_SESSION_FILE (default
.bonpreu-session.json, mode 0600) so the agent can reuse it without credentials.

Run: uv run --env-file .env python login_once.py
"""

import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright

BASE_URL = os.getenv("BONPREU_BASE_URL", "https://energia.bonpreuesclat.cat").rstrip("/")
INVOICES_PATH = os.getenv("BONPREU_INVOICES_PATH", "/factures")
SESSION_FILE = Path(os.getenv("BONPREU_SESSION_FILE", ".bonpreu-session.json"))


async def main() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(f"{BASE_URL}{INVOICES_PATH}", wait_until="domcontentloaded")
        print(f"Log in inside the browser window (complete any 2FA prompt).")
        print(f"The session will be saved to {SESSION_FILE} once you are inside the client area.")
        for _ in range(120):
            await page.wait_for_timeout(5000)
            password_fields = await page.locator("input[type=password]").count()
            if password_fields == 0 and "login" not in page.url.lower():
                break
        await context.storage_state(path=str(SESSION_FILE))
        SESSION_FILE.chmod(0o600)
        await browser.close()
        print(f"Session saved to {SESSION_FILE} (mode 0600). The agent will reuse it.")


if __name__ == "__main__":
    asyncio.run(main())
