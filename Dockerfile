FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml ./
RUN uv sync --no-dev || uv pip install --system .

RUN uv run playwright install --with-deps chromium

COPY main.py login_once.py ./

CMD ["uv", "run", "agents", "serve"]
