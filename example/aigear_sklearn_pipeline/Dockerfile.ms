FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.5.14 /uv /uvx /bin/

WORKDIR /ms
COPY . .

RUN uv python install 3.12.7

RUN uv venv /opt/venv/ms --python 3.12.7
RUN . /opt/venv/ms/bin/activate && uv pip install -r requirements_ms.txt

ENV VIRTUAL_ENV=/opt/venv/ms
ENV PATH="/opt/venv/ms/bin:$PATH"
ENV PORT=50051 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONBUFFERED=1