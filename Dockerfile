FROM python:3.13-alpine AS builder

WORKDIR /build

COPY pyproject.toml .

RUN pip install --no-cache-dir --prefix=/install .

COPY app.py .

FROM python:3.13-alpine

RUN adduser -D -u 1000 appuser

ENV PYTHONUNBUFFERED=1

COPY --from=builder /install /usr/local
WORKDIR /app
COPY app.py .

USER appuser

ENTRYPOINT ["python", "app.py"]
