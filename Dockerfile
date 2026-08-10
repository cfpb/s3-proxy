FROM python:3.13-alpine AS builder

WORKDIR /build

COPY pyproject.toml .

# Add Zscaler public cert, see https://github.com/cfpb/zscaler-cert.
ADD https://raw.githubusercontent.com/cfpb/zscaler-cert/3982ebd9edf9de9267df8d1732ff5a6f88e38375/zscaler_root_ca.pem /usr/local/share/ca-certificates/zscaler_root_ca.crt
RUN update-ca-certificates

RUN apk update --no-cache && apk upgrade --no-cache && \
    pip install --no-cache-dir --prefix=/install .

COPY app.py .

FROM python:3.13-alpine

RUN adduser -D -u 1000 appuser

ENV PYTHONUNBUFFERED=1

COPY --from=builder /install /usr/local
WORKDIR /app
COPY app.py .

# Add Zscaler public cert, see https://github.com/cfpb/zscaler-cert.
ADD https://raw.githubusercontent.com/cfpb/zscaler-cert/3982ebd9edf9de9267df8d1732ff5a6f88e38375/zscaler_root_ca.pem /usr/local/share/ca-certificates/zscaler_root_ca.crt
RUN update-ca-certificates

RUN apk update --no-cache && apk upgrade --no-cache

USER appuser

ENTRYPOINT ["python", "app.py"]
