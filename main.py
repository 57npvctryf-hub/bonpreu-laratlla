import asyncio
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from mistralai import agents

BASE_URL = os.getenv("BONPREU_BASE_URL", "https://energia.bonpreuesclat.cat").rstrip("/")
INVOICES_URL = f"{BASE_URL}{os.getenv('BONPREU_INVOICES_PATH', '/factures')}"
SESSION_FILE = Path(os.getenv("BONPREU_SESSION_FILE", ".bonpreu-session.json"))
DOWNLOAD_DIR = Path(os.getenv("BONPREU_INVOICES_DIR", "invoices"))

EMAIL_ENV = "BONPREU_EMAIL"
PASSWORD_ENV = "BONPREU_PASSWORD"

_AMOUNT_RE = re.compile(
    r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}|\d+(?:\.\d{2})?)\s*(?:€|EUR|euros)",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"\b(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})\b")

_INVOICE_HINT_KEYS = {
    "number", "numero", "num_factura", "invoice_number", "referencia", "reference", "id",
    "fecha", "date", "issue_date", "emission_date", "fecha_emision", "data",
    "vencimiento", "due_date", "fecha_vencimiento",
    "periodo", "period", "billing_period", "periode",
    "importe", "amount", "total", "total_amount", "import_total", "amount_total",
    "estado", "status", "pagada", "paid",
    "pdf", "pdf_url", "url", "download_url", "link",
}

_KEY_ALIASES = {
    "number": "number", "numero": "number", "num_factura": "number",
    "invoice_number": "number", "referencia": "number", "reference": "number",
    "fecha": "date", "date": "date", "issue_date": "date", "emission_date": "date",
    "fecha_emision": "date", "data": "date",
    "vencimiento": "due_date", "due_date": "due_date", "fecha_vencimiento": "due_date",
    "periodo": "period", "period": "period", "billing_period": "period", "periode": "period",
    "importe": "amount", "amount": "amount", "total": "amount", "total_amount": "amount",
    "import_total": "amount", "amount_total": "amount",
    "estado": "status", "status": "status", "pagada": "status", "paid": "status",
    "pdf": "pdf", "pdf_url": "pdf", "download_url": "pdf", "link": "pdf",
    "url": "url", "id": "id",
}


def _credentials() -> tuple[str, str]:
    email = os.environ.get(EMAIL_ENV, "").strip()
    password = os.environ.get(PASSWORD_ENV, "")
    if not email or not password:
        raise agents.ToolError(
            f"Missing credentials: set {EMAIL_ENV} and {PASSWORD_ENV} in the environment "
            "(.env locally, or Studio secret bindings when deployed). "
            "Do not paste them into the chat; the agent never prints them."
        )
    return email, password


def _parse_amount(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = _AMOUNT_RE.search(value.replace(" ", " "))
    if not match:
        return None
    raw = match.group(1)
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _normalize_invoice(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in raw.items():
        alias = _KEY_ALIASES.get(str(key).lower().strip())
        if alias and alias not in out:
            out[alias] = value
    if "amount" in out:
        out["amount"] = _parse_amount(out["amount"])
    return out


def _find_invoice_lists(node: Any) -> list[list[dict[str, Any]]]:
    found: list[list[dict[str, Any]]] = []
    if isinstance(node, dict):
        for value in node.values():
            if isinstance(value, list) and value and all(isinstance(i, dict) for i in value):
                keys = {str(k).lower() for item in value[:10] for k in item}
                if len(keys & _INVOICE_HINT_KEYS) >= 2:
                    found.append(value)
            found.extend(_find_invoice_lists(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_find_invoice_lists(item))
    return found


def _date_sort_key(invoice: dict[str, Any]) -> str:
    date = str(invoice.get("date") or invoice.get("due_date") or "")
    match = _DATE_RE.search(date)
    if not match:
        return ""
    day, month, year = re.split(r"[/.-]", match.group(1))
    year = year.zfill(4) if len(year) == 2 else year
    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"


async def _looks_like_login(page: Any) -> bool:
    try:
        return await page.locator("input[type=password]").first.is_visible(timeout=3000)
    except Exception:
        return False


async def _login(page: Any) -> None:
    email, password = _credentials()
    await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60_000)
    email_locator = page.locator(
        "input[type=email], input[name=email], input[name=login], "
        "input[name=username], input#email, input#login"
    ).first
    if await email_locator.is_visible(timeout=5000):
        await email_locator.fill(email)
    await page.locator("input[type=password]").first.fill(password)
    await page.locator(
        "button[type=submit], input[type=submit], "
        "button:has-text('Inicia'), button:has-text('Entrar'), button:has-text('Accedeix')"
    ).first.click()
    await page.wait_for_load_state("networkidle", timeout=60_000)
    if await _looks_like_login(page):
        raise agents.ToolError(
            "Login did not complete: the portal may require a one-time code (2FA) or the "
            "credentials were rejected. Run `uv run --env-file .env python login_once.py` once "
            f"to save a session to {SESSION_FILE}, or check {EMAIL_ENV}/{PASSWORD_ENV}."
        )


@agents.tool
async def check_latest_invoices(limit: int = 5, download_latest_pdf: bool = False) -> dict[str, Any]:
    """Log into the BonpreuEsclat Energia Oficina Virtual (https://energia.bonpreuesclat.cat)
    and return the customer's latest electricity invoices.

    Reuses the saved session in BONPREU_SESSION_FILE when present, otherwise logs in with
    BONPREU_EMAIL / BONPREU_PASSWORD. Each invoice includes number, billing period, issue
    date, due date, amount, payment status and PDF link when available.

    Args:
        limit: maximum number of recent invoices to return (default 5).
        download_latest_pdf: also download the newest invoice PDF into BONPREU_INVOICES_DIR.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise agents.ToolError(
            "playwright is not installed: run `uv add playwright && uv run playwright install chromium`"
        ) from exc

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    captured_urls: list[str] = []
    invoices: list[dict[str, Any]] = []
    try:
        session_reused = SESSION_FILE.exists()
        context = await browser.new_context(
            storage_state=str(SESSION_FILE) if session_reused else None
        )
        page = await context.new_page()

        def on_response(response: Any) -> None:
            url = response.url.lower()
            if any(word in url for word in ("factura", "invoice", "bill")):
                captured_urls.append(response.url)
                if response.headers.get("content-type", "").startswith("application/json"):
                    try:
                        payload = response.json()
                    except Exception:
                        payload = None
                    if payload is not None:
                        for lst in _find_invoice_lists(payload):
                            invoices.extend(_normalize_invoice(item) for item in lst)

        page.on("response", on_response)
        await page.goto(INVOICES_URL, wait_until="domcontentloaded", timeout=60_000)
        if await _looks_like_login(page):
            await _login(page)
        await page.goto(INVOICES_URL, wait_until="networkidle", timeout=60_000)
        await page.wait_for_timeout(2000)

        if not invoices:
            text = await page.inner_text("body")
            for line in text.splitlines():
                date_match = _DATE_RE.search(line)
                amount_match = _AMOUNT_RE.search(line)
                if date_match and amount_match:
                    invoices.append(
                        {
                            "raw": line.strip()[:200],
                            "date": date_match.group(1),
                            "amount": _parse_amount(amount_match.group(0)),
                        }
                    )

        seen: set[tuple] = set()
        unique: list[dict[str, Any]] = []
        for invoice in invoices:
            key = (
                invoice.get("number"),
                invoice.get("date"),
                invoice.get("amount"),
                invoice.get("raw"),
            )
            if key not in seen:
                seen.add(key)
                unique.append(invoice)
        unique.sort(key=_date_sort_key, reverse=True)
        latest = unique[: max(1, limit)]

        await context.storage_state(path=str(SESSION_FILE))
        SESSION_FILE.chmod(0o600)

        result: dict[str, Any] = {
            "portal": INVOICES_URL,
            "session_reused": session_reused,
            "count": len(latest),
            "invoices": latest,
            "api_endpoints_seen": sorted(set(captured_urls))[:20],
        }

        if download_latest_pdf and latest:
            pdf_url = next((i.get("pdf") or i.get("url") for i in latest if i.get("pdf") or i.get("url")), None)
            if pdf_url:
                absolute = urljoin(INVOICES_URL + "/", str(pdf_url))
                response = await context.request.get(absolute, timeout=60_000)
                if response.ok:
                    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
                    name = str(latest[0].get("number") or "latest").replace("/", "-")
                    path = DOWNLOAD_DIR / f"factura-{name}.pdf"
                    await path.write_bytes(await response.body())
                    result["downloaded_pdf"] = str(path)
                else:
                    result["pdf_download_failed"] = f"HTTP {response.status} for {absolute}"
            else:
                result["pdf_download_failed"] = "no PDF link found on the latest invoice"

        return result
    finally:
        await browser.close()
        await playwright.stop()


agent = agents.Agent(
    model="mistral-large-4",
    name="bonpreu-invoices-agent",
    instructions=(
        "You check the latest electricity invoices of a BonpreuEsclat Energia customer in their "
        "Oficina Virtual (https://energia.bonpreuesclat.cat/factures). Call check_latest_invoices "
        "to fetch the data, then answer in the user's language with a clear summary of the newest "
        "invoices: number, billing period, issue date, due date, amount, payment status and PDF "
        "link or downloaded file. Highlight unpaid or overdue invoices first. Never print, log or "
        "repeat credentials; if the tool reports missing credentials or a required one-time code, "
        "explain how to fix it (set BONPREU_EMAIL/BONPREU_PASSWORD, or run login_once.py once to "
        "save a session) instead of asking for the password in chat."
    ),
    tools=[check_latest_invoices],
)


async def main() -> None:
    result = await agent.run("Quines són les meves últimes factures d'energia? Resum-me-les.")
    print(result.text)


if __name__ == "__main__":
    asyncio.run(main())
