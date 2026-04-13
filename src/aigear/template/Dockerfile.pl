FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.5.14 /uv /uvx /bin/

ENV VENV_PL=/opt/venv/pl

WORKDIR /pl

COPY requirements_pl.txt aigear-0.1.0-py3-none-any.whl ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv ${VENV_PL} --python 3.12.7 \
 && uv pip install --python ${VENV_PL} -r requirements_pl.txt aigear-0.1.0-py3-none-any.whl

COPY . .

ENV PORT=50051 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1