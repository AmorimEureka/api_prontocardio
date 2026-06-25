FROM python:3.12-slim
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG POETRY_VERSION=2.4.1

ENV POETRY_VIRTUALENVS_CREATE=false \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY setup_instant_client.sh /tmp/setup_instant_client.sh
RUN chmod +x /tmp/setup_instant_client.sh \
    && /tmp/setup_instant_client.sh \
    && rm /tmp/setup_instant_client.sh

ENV LD_LIBRARY_PATH=/opt/oracle/instantclient_19_23:${LD_LIBRARY_PATH}
ENV PATH=/opt/oracle/instantclient_19_23:${PATH}

WORKDIR /app
COPY pyproject.toml poetry.lock ./

RUN pip install \
    --no-cache-dir \
    --timeout 120 \
    --retries 10 \
    --index-url "${PIP_INDEX_URL}" \
    "poetry==${POETRY_VERSION}" \
    && poetry config installer.max-workers 10 \
    && poetry source add --priority=primary docker-pypi "${PIP_INDEX_URL}" \
    && poetry lock --no-interaction --no-ansi \
    && poetry install --no-interaction --no-ansi --without dev --no-root

COPY . .

EXPOSE 8000
CMD ["sh", "-c", "poetry run uvicorn --host 0.0.0.0 --port ${PORT:-8000} app_prontocardio.app:app"]
