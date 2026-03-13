FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.5.14 /uv /uvx /bin/

WORKDIR /pl
COPY . .

RUN uv python install 3.12.7

RUN uv venv /opt/venv/pl --python 3.12.7
RUN . /opt/venv/pl/bin/activate && uv pip install -r requirements_pl.txt

ENV VIRTUAL_ENV=/opt/venv/pl
ENV PATH="/opt/venv/pl/bin:$PATH"
ENV PORT=50051 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONBUFFERED=1