import logging
import os
import sys
from datetime import timezone

import aiobotocore.session
from aiohttp import web
from email.utils import format_datetime, parsedate_to_datetime


S3_BUCKET = os.getenv("S3_BUCKET")
PORT = int(os.getenv("PORT", "8080"))

# These content types are displayed in the browser.
INLINE_CONTENT_TYPES = frozenset({"application/pdf", "text/plain"})

SECURITY_HEADERS = {
    # Disable embedded content in served files.
    "Content-Security-Policy": "default-src 'none'",
    # Force browsers to respect the served content type.
    "X-Content-Type-Options": "nosniff",
}

PASSTHROUGH_HEADERS = {
    "CacheControl": "Cache-Control",
    "ContentEncoding": "Content-Encoding",
    "ContentLanguage": "Content-Language",
    "ContentRange": "Content-Range",
    "ETag": "ETag",
    "Expires": "Expires",
    "LastModified": "Last-Modified",
}


logger = logging.getLogger("s3-proxy")


async def s3_client_ctx(app: web.Application):  # pragma: no cover
    session = aiobotocore.session.get_session()
    async with session.create_client("s3") as s3_client:
        app["s3_client"] = s3_client
        logger.info(f"S3 proxy started for bucket {S3_BUCKET} on port {PORT}")
        yield


async def healthz(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def http_404() -> web.Response:
    return web.Response(status=404, text="Not Found")


def _get_s3_key_from_path(path: str) -> str:
    return path.lstrip("/")


def _get_content_disposition(content_type: str | None) -> str:
    return (
        "inline"
        if (content_type and content_type.split(";")[0].strip() in INLINE_CONTENT_TYPES)
        else "attachment"
    )


def _prep_s3_kwargs(key: str, request: web.Request) -> dict:
    kwargs = {
        "Bucket": S3_BUCKET,
        "Key": key,
    }

    if if_none_match := request.headers.get("If-None-Match"):
        kwargs["IfNoneMatch"] = if_none_match

    if if_modified_since := request.headers.get("If-Modified-Since"):
        try:
            kwargs["IfModifiedSince"] = parsedate_to_datetime(if_modified_since)
        except (TypeError, ValueError):
            pass

    if range_header := request.headers.get("Range"):
        kwargs["Range"] = range_header

    return kwargs


async def _strip_server_header(
    request: web.Request, response: web.StreamResponse
) -> None:
    response.headers.pop("Server", None)


async def handle_request(request: web.Request) -> web.StreamResponse:
    key = _get_s3_key_from_path(request.path)

    if not key:
        return http_404()

    # Make request to S3.
    s3_client = request.app["s3_client"]
    kwargs = _prep_s3_kwargs(key, request)

    try:
        response = await s3_client.get_object(**kwargs)
    except s3_client.exceptions.NoSuchKey:
        return http_404()
    except Exception as e:
        status = (
            getattr(e, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
        )

        if status in (304,):
            return web.Response(status=status, headers=dict(SECURITY_HEADERS))

        logger.exception("S3 error for key=%s", key)
        return web.Response(status=502, text="Bad Gateway")

    # Build response headers.
    headers = dict(SECURITY_HEADERS)

    content_type = response.get("ContentType", "application/octet-stream")
    headers["Content-Type"] = content_type
    headers["Content-Disposition"] = _get_content_disposition(content_type)

    if (content_length := response.get("ContentLength")) is not None:
        headers["Content-Length"] = str(content_length)

    for key, header in PASSTHROUGH_HEADERS.items():
        if value := response.get(key):
            if key == "LastModified":
                headers[header] = format_datetime(
                    value.astimezone(timezone.utc), usegmt=True
                )
            else:
                headers[header] = str(value)

    # Determine status code.
    status = (
        206
        if (
            response.get("ContentRange")
            or response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 206
        )
        else 200
    )

    # Return headers only for HEAD requests.
    body = response["Body"]

    if request.method == "HEAD":
        body.close()
        return web.Response(status=status, headers=headers)

    # Stream the body contents for GET requests.
    response = web.StreamResponse(status=status, headers=headers)

    await response.prepare(request)

    try:
        async for chunk in body:
            await response.write(chunk)
    finally:
        body.close()

    await response.write_eof()
    return response


def create_app() -> web.Application:
    if not S3_BUCKET:
        logger.error("S3_BUCKET environment variable must be set")
        sys.exit(1)

    app = web.Application()
    app.cleanup_ctx.append(s3_client_ctx)
    app.on_response_prepare.append(_strip_server_header)

    app.router.add_get("/healthz", healthz)
    app.router.add_get("/{path:.*}", handle_request)

    return app


def main() -> None:  # pragma: no cover
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
