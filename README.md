# bonpreu-invoices-agent

A Mistral Agents SDK agent that logs into the BonpreuEsclat Energia Oficina Virtual
(`https://energia.bonpreuesclat.cat`, reached from `https://www.bonpreuesclat.cat/energia` → "Àrea client")
and checks your latest electricity invoices (number, period, dates, amount, payment status, PDF).

It uses headless Chromium (Playwright) because the Oficina Virtual is a JavaScript app.
Credentials are read only from environment variables (`.env` locally, Studio secret bindings when
deployed) and are never printed, logged or committed. A logged-in session is cached in
`BONPREU_SESSION_FILE` (`storage_state`, mode 0600) so repeat runs skip the login — useful when
the portal asks for a one-time code (2FA).

## Setup

```bash
uv sync
uv run playwright install chromium
cp .env.example .env   # fill MISTRAL_API_KEY, BONPREU_EMAIL, BONPREU_PASSWORD
```

## Run

```bash
# one-off check
uv run --env-file .env python main.py

# interactive chat
uv run --env-file .env agents chat
```

If the portal requires a one-time code (2FA) that the headless login cannot complete, log in
once manually and let the agent reuse the session:

```bash
uv run --env-file .env python login_once.py
```

## Deploy

```bash
agents deploy --secret-binding BONPREU_EMAIL=<secret-name> --secret-binding BONPREU_PASSWORD=<secret-name>
```

The included `Dockerfile` installs the dependencies and the Chromium browser for the worker.

## Configuration (env vars)

| Var | Default | Purpose |
| --- | --- | --- |
| `MISTRAL_API_KEY` | — | Mistral API key (required) |
| `BONPREU_EMAIL` / `BONPREU_PASSWORD` | — | BonpreuEsclat account (same as the app / www login) |
| `BONPREU_BASE_URL` | `https://energia.bonpreuesclat.cat` | Oficina Virtual base URL |
| `BONPREU_INVOICES_PATH` | `/factures` | Invoices route |
| `BONPREU_SESSION_FILE` | `.bonpreu-session.json` | Cached browser session (gitignored, 0600) |
| `BONPREU_INVOICES_DIR` | `invoices` | Where the latest PDF is saved (gitignored) |

## Notes

- The portal is a JS single-page app, so the tool reads invoice data from the JSON API calls
  the page makes (and falls back to scraping rendered rows). The exact response shape may
  change; when it does, `api_endpoints_seen` in the tool result shows which endpoints were hit.
- Never put credentials in code, chat, logs or git. `.env`, the session file and downloaded
  invoices are gitignored.
