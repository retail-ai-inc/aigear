FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /uvx /bin/

# Fixed by aigear/common/constant.py (VENV_BASE_DIR). Do NOT change.
ENV VENV_BASE=/opt/venv \
    UV_LINK_MODE=copy

# venv name must match venv_ms in env.json. Add one block per pipeline.
ENV VENV_MS=${VENV_BASE}/ms

WORKDIR /ms

COPY requirements_ms.txt aigear-0.1.0-py3-none-any.whl ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv ${VENV_MS} --python 3.12.7 \
 && uv pip install --python ${VENV_MS} -r requirements_ms.txt aigear-0.1.0-py3-none-any.whl

COPY . .

ENV PORT=50051 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
