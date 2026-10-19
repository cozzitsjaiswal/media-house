FROM ghcr.io/astral-sh/uv:0.13.0 AS uv
FROM python:3.11.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY .python-version pyproject.toml uv.lock README.md ./
COPY media_house ./media_house
COPY tests ./tests
COPY media_house_complete_documentation/media_house_starter/schemas/client_requirements.schema.json ./media_house_complete_documentation/media_house_starter/schemas/client_requirements.schema.json

RUN uv sync --locked --no-dev --no-editable \
    && python -m unittest discover -s tests -v \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown -R app:app /app /data

USER app
VOLUME ["/data"]
ENV MEDIA_HOUSE_DB=/data/media_house.sqlite3

ENTRYPOINT ["media-house"]
