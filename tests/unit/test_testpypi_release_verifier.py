import hashlib
import json
import urllib.error
from email.message import Message

import pytest

from scripts.verify_testpypi_release import (
    DISTRIBUTION_NAME,
    DISTRIBUTION_VERSION,
    EXPECTED_FILENAMES,
    VerificationError,
    poll_metadata,
    validate_metadata,
)


class Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class FakeTime:
    def __init__(self) -> None:
        self.value = 0.0

    def clock(self) -> float:
        return self.value

    def sleep(self, duration: float) -> None:
        self.value += duration


def metadata(*, filenames: set[str] | None = None, digest: str = "a" * 64) -> dict[str, object]:
    return {
        "info": {"name": DISTRIBUTION_NAME, "version": DISTRIBUTION_VERSION},
        "urls": [
            {
                "filename": filename,
                "url": f"https://files.test/{filename}",
                "digests": {"sha256": digest},
            }
            for filename in sorted(filenames or EXPECTED_FILENAMES)
        ],
    }


def test_poll_metadata_retries_temporary_404_then_returns_metadata() -> None:
    responses: list[Response | urllib.error.HTTPError] = [
        urllib.error.HTTPError("https://test", 404, "not found", Message(), None),
        Response(json.dumps(metadata()).encode()),
    ]
    time = FakeTime()

    def opener(_: str, __: float) -> Response:
        response = responses.pop(0)
        if isinstance(response, urllib.error.HTTPError):
            raise response
        assert isinstance(response, Response)
        return response

    assert (
        poll_metadata(
            "https://test",
            timeout_seconds=1,
            deadline_seconds=3,
            retry_interval_seconds=1,
            opener=opener,
            clock=time.clock,
            sleep=time.sleep,
        )
        == metadata()
    )
    assert time.value == 1


def test_poll_metadata_fails_clearly_after_permanent_404_timeout() -> None:
    time = FakeTime()

    def opener(_: str, __: float) -> Response:
        raise urllib.error.HTTPError("https://test", 404, "not found", Message(), None)

    with pytest.raises(VerificationError, match="propagation deadline"):
        poll_metadata(
            "https://test",
            timeout_seconds=1,
            deadline_seconds=2,
            retry_interval_seconds=1,
            opener=opener,
            clock=time.clock,
            sleep=time.sleep,
        )
    assert time.value == 2


def test_metadata_digest_mismatch_fails_immediately() -> None:
    hashes = {filename: "b" * 64 for filename in EXPECTED_FILENAMES}

    with pytest.raises(VerificationError, match="SHA-256 mismatch"):
        validate_metadata(metadata(digest="a" * 64), hashes)


def test_unexpected_metadata_file_set_fails_immediately() -> None:
    hashes = {
        filename: hashlib.sha256(filename.encode()).hexdigest() for filename in EXPECTED_FILENAMES
    }

    with pytest.raises(VerificationError, match="filenames do not match"):
        validate_metadata(metadata(filenames={"unexpected.whl"}), hashes)
