FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.5.14 /uv /uvx /bin/

WORKDIR /{project_name}
COPY . .

RUN uv python install 3.10.16

RUN uv venv /opt/venv/{project_name} --python 3.10.16
RUN . /opt/venv/{project_name}/bin/activate && \
    uv pip install --upgrade setuptools && \
    uv pip install -r requirements.txt

ENV PORT=50051 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONBUFFERED=1