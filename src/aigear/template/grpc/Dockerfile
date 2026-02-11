FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.5.14 /uv /uvx /bin/

WORKDIR /grpc
COPY /grpc .
COPY env.json .

RUN uv python install 3.8.16

RUN uv venv /opt/venv/{projrct_name} --python 3.8.16
RUN uv exec --venv /opt/venv/{projrct_name} -- pip install -r requirements.txt

ENV VIRTUAL_ENV=/opt/venv/{projrct_name}
ENV PATH="/opt/venv/{projrct_name}/bin:$PATH"
ENV PORT=50051 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONBUFFERED=1
