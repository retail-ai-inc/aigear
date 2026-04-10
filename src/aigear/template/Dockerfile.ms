FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.5.14 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/opt/venv/ms \
    PATH="/opt/venv/ms/bin:$PATH"

WORKDIR /ms

COPY requirements_ms.txt aigear-0.1.0-py3-none-any.whl ./
RUN uv venv /opt/venv/ms --python 3.12.7 \
 && uv pip install --no-cache -r requirements_ms.txt \
 && uv pip install --no-cache aigear-0.1.0-py3-none-any.whl

COPY . .

ENV PORT=50051 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1