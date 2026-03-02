# s3-proxy!

A simple Docker image that proxies HTTP requests to an S3 bucket.

## Behavior

`GET /foo/bar.txt` streams `s3://<bucket>/foo/bar.txt` to the client. `HEAD` requests are also supported. All other request types are rejected.

Docker container health check is available at `GET /healthz`.

Conditional request headers (`If-None-Match`, `If-Modified-Since`) are forwarded to S3, returning `304 Not Modified` when appropriate. Range requests (`Range`) are also forwarded, returning `206 Partial Content` when appropriate.

The `Content-Disposition` response header is set to `inline` for `application/pdf` and `text/plain` responses so that these file types open directly in web browsers. All other content types have this header set to `attachment` to prompt a download.

Select S3 response headers are passed through to the client: `Cache-Control`, `Content-Encoding`, `Content-Language`, `Content-Range`, `ETag`, `Expires`, `Last-Modified`.

All responses also include `Content-Security-Policy: default-src 'none'` and `X-Content-Type-Options: nosniff` to prevent script execution and MIME type sniffing if a malicious file is served from the bucket.

## Usage

### Build the image

```bash
docker build -t s3-proxy .
```

### Run the image

```bash
# AWS credentials must be set via environment variables.
docker run -e S3_BUCKET=my-bucket-name -p 8080:8080 s3-proxy
```

### Run unit tests

```bash
pip install -e ".[dev]"
pytest
```

### Pre-commit hooks

This repository includes
[pre-commit](https://pre-commit.com/)
hooks to enforce consistent Python linting and formatting on every commit.

To install the hooks, run:

```sh
pre-commit install
```

Going forward, the hooks will run on all local commits.

At any time to run the hooks on all files in the project you can run:

```sh
pre-commit run --all-files
```

These hooks also run against all PRs to this repo.
