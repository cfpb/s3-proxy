import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

os.environ["S3_BUCKET"] = "test-bucket"

import pytest
from dateutil.tz import tzutc

from app import _get_content_disposition, _get_s3_key_from_path, create_app


class MockNoSuchKey(Exception):
    pass


class FakeStreamingBody:
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.closed = False

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self.chunks:
            yield chunk

    def close(self):
        self.closed = True


def _fake_s3_response(
    body: bytes = b"Hello world",
    status_code: int = 200,
    **headers,
) -> dict:
    response = {
        "ContentType": "text/plain",
        "ContentLength": len(body),
        "Body": FakeStreamingBody([body]),
        "ResponseMetadata": {"HTTPStatusCode": status_code},
    }

    response.update(headers)

    return response


def _mock_s3_client(side_effect=None, return_value=None) -> AsyncMock:
    client = AsyncMock()
    client.exceptions = MagicMock()
    client.exceptions.NoSuchKey = MockNoSuchKey

    if side_effect:
        client.get_object.side_effect = side_effect
    elif return_value is not None:
        client.get_object.return_value = return_value
    else:
        client.get_object.return_value = _fake_s3_response()
    return client


@pytest.fixture
async def app_fixture():
    app = create_app()
    app.cleanup_ctx.clear()
    app["s3_client"] = _mock_s3_client()
    return app


@pytest.fixture
async def client_fixture(app_fixture, aiohttp_client):
    return await aiohttp_client(app_fixture)


class TestGetS3KeyFromPath:
    def test_no_leading_slash(self):
        assert _get_s3_key_from_path("foo.txt") == "foo.txt"

    def test_strips_leading_slash(self):
        assert _get_s3_key_from_path("/foo.txt") == "foo.txt"

    def test_strips_multiple_leading_slashes(self):
        assert _get_s3_key_from_path("///foo.txt") == "foo.txt"

    def test_only_slash(self):
        assert _get_s3_key_from_path("/") == ""

    def test_empty_string(self):
        assert _get_s3_key_from_path("") == ""


class TestGetContentDisposition:
    def test_none_attachment(self):
        assert _get_content_disposition(None) == "attachment"

    def pdf_inline(self):
        assert _get_content_disposition("application/pdf") == "inline"

    def text_inline(self):
        assert _get_content_disposition("text/plain") == "inline"

    def test_with_charset_inline(self):
        assert _get_content_disposition("text/plain; charset=utf-8") == "inline"

    def test_html_attachment(self):
        assert _get_content_disposition("text/html") == "attachment"

    def test_image_attachment(self):
        assert _get_content_disposition("image/png") == "attachment"


class TestForbiddenMethods:
    async def test_post_not_allowed(self, client_fixture):
        resp = await client_fixture.post("/foo.txt")
        assert resp.status == 405

    async def test_put_not_allowed(self, client_fixture):
        resp = await client_fixture.put("/foo.txt")
        assert resp.status == 405

    async def test_delete_not_allowed(self, client_fixture):
        resp = await client_fixture.delete("/foo.txt")
        assert resp.status == 405


class TestHealthCheck:
    async def test_healthz_returns_200(self, client_fixture):
        response = await client_fixture.get("/healthz")
        assert response.status == 200
        text = await response.text()
        assert text == "ok"


class TestRequests:
    async def test_simple_get(self, client_fixture):
        response = await client_fixture.get("/test.txt")
        assert response.status == 200

        assert response.headers["Content-Security-Policy"] == "default-src 'none'"
        assert response.headers["X-Content-Type-Options"] == "nosniff"

        body = await response.read()
        assert body == b"Hello world"

    async def test_get_404_at_root(self, client_fixture):
        response = await client_fixture.get("/")
        assert response.status == 404

    async def test_get_404_no_such_key(self, app_fixture, aiohttp_client):
        app_fixture["s3_client"] = _mock_s3_client(side_effect=MockNoSuchKey("missing"))
        client = await aiohttp_client(app_fixture)

        response = await client.get("/does/not/exist.txt")
        assert response.status == 404

    async def test_get_304(self, app_fixture, aiohttp_client):
        exc = Exception("Not modified")
        setattr(exc, "response", {"ResponseMetadata": {"HTTPStatusCode": 304}})

        app_fixture["s3_client"] = _mock_s3_client(side_effect=exc)
        client = await aiohttp_client(app_fixture)

        response = await client.get("/missing.txt", headers={"If-None-Match": '"abcd"'})
        assert response.status == 304

        assert response.headers["Content-Security-Policy"] == "default-src 'none'"
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    async def test_get_unhandled_exception(self, app_fixture, aiohttp_client):
        exc = Exception("Unhandled")
        setattr(exc, "response", {"ResponseMetadata": {"HTTPStatusCode": 500}})

        app_fixture["s3_client"] = _mock_s3_client(side_effect=exc)
        client = await aiohttp_client(app_fixture)

        response = await client.get("/error.txt")
        assert response.status == 502

    async def test_head(self, client_fixture):
        response = await client_fixture.head("/test.txt")
        assert response.status == 200

        assert response.headers["Content-Security-Policy"] == "default-src 'none'"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Content-Type"] == "text/plain"

        # HEAD requests should return an empty body.
        body = await response.read()
        assert body == b""

    async def test_passthrough_response_headers(self, app_fixture, aiohttp_client):
        test_body = b"Hello from PDF"
        app_fixture["s3_client"] = _mock_s3_client(
            return_value=_fake_s3_response(
                body=test_body,
                CacheControl="public, max-age=3600",
                ContentEncoding="gzip",
                ContentLanguage="en",
                ContentType="application/pdf",
                ContentLength=len(test_body),
                ETag='"abcde12345"',
                Expires="Wed, 21 Oct 2015 07:28:00 GMT",
                InvalidHeader="this is invalid",
                LastModified=datetime(2015, 10, 21, 7, 28, 0, tzinfo=tzutc()),
            )
        )
        client = await aiohttp_client(app_fixture)

        response = await client.get("/test.pdf")

        assert response.headers["Cache-Control"] == "public, max-age=3600"
        assert response.headers["Content-Disposition"] == "inline"
        assert response.headers["Content-Encoding"] == "gzip"
        assert response.headers["Content-Language"] == "en"
        assert response.headers["Content-Length"] == "14"
        assert response.headers["Content-Type"] == "application/pdf"
        assert response.headers["ETag"] == '"abcde12345"'
        assert response.headers["Expires"] == "Wed, 21 Oct 2015 07:28:00 GMT"
        assert response.headers["Last-Modified"] == "Wed, 21 Oct 2015 07:28:00 GMT"

        assert "InvalidHeader" not in response.headers

    async def test_get_null_content_length(self, app_fixture, aiohttp_client):
        app_fixture["s3_client"] = _mock_s3_client(
            return_value=_fake_s3_response(ContentLength=None)
        )
        client = await aiohttp_client(app_fixture)

        response = await client.get("/test.txt")
        assert response.status == 200

        assert "Content-Length" not in response.headers

    async def test_s3_request_if_none_match(self, app_fixture, aiohttp_client):
        app_fixture["s3_client"] = _mock_s3_client()
        client = await aiohttp_client(app_fixture)

        await client.get("/test.txt", headers={"If-None-Match": '"abcde12345"'})
        s3_call_kwargs = app_fixture["s3_client"].get_object.call_args[1]
        assert s3_call_kwargs["IfNoneMatch"] == '"abcde12345"'

    async def test_s3_request_if_modified_since(self, app_fixture, aiohttp_client):
        app_fixture["s3_client"] = _mock_s3_client()
        client = await aiohttp_client(app_fixture)

        await client.get(
            "/test.txt", headers={"If-Modified-Since": "Wed, 21 Oct 2015 07:28:00 GMT"}
        )
        s3_call_kwargs = app_fixture["s3_client"].get_object.call_args[1]
        assert s3_call_kwargs["IfModifiedSince"] == datetime(
            2015, 10, 21, 7, 28, 0, tzinfo=timezone.utc
        )

        await client.get("/test.txt", headers={"If-Modified-Since": "invalid-date"})
        s3_call_kwargs = app_fixture["s3_client"].get_object.call_args[1]
        assert "IfModifiedSince" not in s3_call_kwargs

    async def test_range_requests(self, app_fixture, aiohttp_client):
        app_fixture["s3_client"] = _mock_s3_client(
            return_value=_fake_s3_response(
                body=b"Hello", status_code=206, ContentRange="bytes 0-4/11"
            )
        )
        client = await aiohttp_client(app_fixture)

        response = await client.get("/test.txt", headers={"Range": "bytes=0-4"})
        assert response.status == 206
        assert response.headers["Content-Range"] == "bytes 0-4/11"


class TestUndefinedBucket:
    async def test_missing_bucket_exits(self, monkeypatch):
        import app

        monkeypatch.setattr(app, "S3_BUCKET", "")
        with pytest.raises(SystemExit):
            app.create_app()
