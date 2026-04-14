FROM python:3.13-alpine AS builder

WORKDIR /build

COPY pyproject.toml .

RUN apk update --no-cache && apk upgrade --no-cache && \
    pip install --no-cache-dir --prefix=/install .

COPY app.py .

FROM python:3.13-alpine

RUN adduser -D -u 1000 appuser

ENV PYTHONUNBUFFERED=1

COPY --from=builder /install /usr/local
WORKDIR /app
COPY app.py .

RUN apk update --no-cache && apk upgrade --no-cache

USER appuser

ENTRYPOINT ["python", "app.py"]
