"""Verify an exact TestPyPI release using standard-library HTTP clients only."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

DISTRIBUTION_NAME = "mnemo-unified-context"
DISTRIBUTION_VERSION = "0.1.0a2"
WHEEL_FILENAME = "mnemo_unified_context-0.1.0a2-py3-none-any.whl"
SDIST_FILENAME = "mnemo_unified_context-0.1.0a2.tar.gz"
EXPECTED_FILENAMES = frozenset({WHEEL_FILENAME, SDIST_FILENAME})


class VerificationError(ValueError):
    """A release verification failure that must not be retried."""


class MetadataUnavailable(ValueError):
    """Expected temporary metadata propagation has not completed yet."""


class HttpResponse(Protocol):
    def read(self) -> bytes: ...

    def __enter__(self) -> HttpResponse: ...

    def __exit__(self, *args: object) -> object: ...


UrlOpen = Callable[[str, float], HttpResponse]
Sleep = Callable[[float], None]
Clock = Callable[[], float]


def _urlopen(url: str, timeout: float) -> HttpResponse:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    return cast(HttpResponse, urllib.request.urlopen(request, timeout=timeout))


def expected_hashes(release_dir: Path) -> dict[str, str]:
    manifest = release_dir / "SHA256SUMS"
    if not manifest.is_file():
        raise VerificationError(f"checksum manifest is missing: {manifest}")

    hashes: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 2:
            raise VerificationError(f"invalid checksum manifest line: {line!r}")
        digest, filename = parts
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise VerificationError(f"invalid SHA-256 digest for {filename!r}")
        if filename in hashes:
            raise VerificationError(f"duplicate checksum entry: {filename}")
        hashes[filename] = digest
    if set(hashes) != EXPECTED_FILENAMES:
        raise VerificationError(
            "checksum manifest must contain exactly expected artifacts: "
            f"{', '.join(sorted(EXPECTED_FILENAMES))}"
        )
    return hashes


def _metadata_from_bytes(payload: bytes, registry_name: str) -> Mapping[str, object]:
    try:
        metadata = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(
            f"{registry_name} returned invalid version metadata JSON"
        ) from error
    if not isinstance(metadata, dict):
        raise VerificationError(f"{registry_name} version metadata must be a JSON object")
    return metadata


def fetch_metadata_once(
    metadata_url: str,
    timeout_seconds: float,
    registry_name: str,
    opener: UrlOpen = _urlopen,
) -> Mapping[str, object]:
    try:
        with opener(metadata_url, timeout_seconds) as response:
            return _metadata_from_bytes(response.read(), registry_name)
    except urllib.error.HTTPError as error:
        if error.code == 404 or 500 <= error.code <= 599:
            raise MetadataUnavailable(
                f"{registry_name} metadata is temporarily unavailable (HTTP {error.code})"
            ) from error
        raise VerificationError(
            f"{registry_name} metadata request failed with HTTP {error.code}"
        ) from error
    except urllib.error.URLError as error:
        raise MetadataUnavailable(
            f"{registry_name} metadata request had a transient network failure"
        ) from error


def poll_metadata(
    metadata_url: str,
    timeout_seconds: float,
    deadline_seconds: float,
    retry_interval_seconds: float,
    *,
    registry_name: str = "TestPyPI",
    opener: UrlOpen = _urlopen,
    clock: Clock = time.monotonic,
    sleep: Sleep = time.sleep,
) -> Mapping[str, object]:
    deadline = clock() + deadline_seconds
    attempts = 0
    last_error: MetadataUnavailable | None = None
    while True:
        attempts += 1
        try:
            return fetch_metadata_once(metadata_url, timeout_seconds, registry_name, opener)
        except MetadataUnavailable as error:
            last_error = error
            remaining = deadline - clock()
            if remaining <= 0:
                break
            sleep(min(retry_interval_seconds, remaining))
    raise VerificationError(
        f"{registry_name} metadata did not become available before the propagation deadline "
        f"after {attempts} attempts: {last_error}"
    )


@dataclass(frozen=True)
class UploadedArtifact:
    filename: str
    sha256: str
    url: str


def validate_metadata(
    metadata: Mapping[str, object], hashes: Mapping[str, str], registry_name: str = "TestPyPI"
) -> tuple[UploadedArtifact, ...]:
    info = metadata.get("info")
    if not isinstance(info, dict):
        raise VerificationError(f"{registry_name} metadata is missing its info object")
    if info.get("name") != DISTRIBUTION_NAME:
        raise VerificationError(
            f"{registry_name} metadata distribution name does not match the release"
        )
    if info.get("version") != DISTRIBUTION_VERSION:
        raise VerificationError(f"{registry_name} metadata version does not match the release")

    urls = metadata.get("urls")
    if not isinstance(urls, list):
        raise VerificationError(f"{registry_name} metadata is missing its artifact URL list")
    uploaded: dict[str, UploadedArtifact] = {}
    for item in urls:
        if not isinstance(item, dict):
            raise VerificationError(f"{registry_name} metadata contains an invalid artifact record")
        filename, url, digests = item.get("filename"), item.get("url"), item.get("digests")
        if (
            not isinstance(filename, str)
            or not isinstance(url, str)
            or not isinstance(digests, dict)
        ):
            raise VerificationError(f"{registry_name} metadata artifact record is incomplete")
        digest = digests.get("sha256")
        if not isinstance(digest, str):
            raise VerificationError(
                f"{registry_name} metadata lacks a SHA-256 digest for {filename!r}"
            )
        if filename in uploaded:
            raise VerificationError(
                f"{registry_name} metadata contains duplicate artifact {filename!r}"
            )
        uploaded[filename] = UploadedArtifact(filename, digest, url)

    if set(uploaded) != set(hashes):
        raise VerificationError(
            f"{registry_name} artifact filenames do not match the expected release: "
            f"expected {sorted(hashes)}, got {sorted(uploaded)}"
        )
    for filename, digest in hashes.items():
        if uploaded[filename].sha256 != digest:
            raise VerificationError(f"{registry_name} SHA-256 mismatch for {filename}")
    return tuple(uploaded[filename] for filename in sorted(uploaded))


def download_verified_artifacts(
    artifacts: tuple[UploadedArtifact, ...],
    destination: Path,
    timeout_seconds: float,
    opener: UrlOpen = _urlopen,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for artifact in artifacts:
        try:
            with opener(artifact.url, timeout_seconds) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            raise VerificationError(
                f"artifact download failed for {artifact.filename} with HTTP {error.code}"
            ) from error
        except urllib.error.URLError as error:
            raise VerificationError(f"artifact download failed for {artifact.filename}") from error
        actual = hashlib.sha256(payload).hexdigest()
        if actual != artifact.sha256:
            raise VerificationError(f"downloaded SHA-256 mismatch for {artifact.filename}")
        (destination / artifact.filename).write_bytes(payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--download-dir", type=Path, required=True)
    parser.add_argument(
        "--metadata-url",
        default="https://test.pypi.org/pypi/mnemo-unified-context/0.1.0a2/json",
    )
    parser.add_argument("--registry-name", default="TestPyPI")
    parser.add_argument("--request-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--deadline-seconds", type=float, default=120.0)
    parser.add_argument("--retry-interval-seconds", type=float, default=3.0)
    args = parser.parse_args()
    if (
        args.request_timeout_seconds <= 0
        or args.deadline_seconds <= 0
        or args.retry_interval_seconds <= 0
    ):
        parser.error("request timeout, deadline, and retry interval must be positive")
    return args


def main() -> None:
    args = parse_args()
    try:
        hashes = expected_hashes(args.release_dir)
        metadata = poll_metadata(
            args.metadata_url,
            args.request_timeout_seconds,
            args.deadline_seconds,
            args.retry_interval_seconds,
            registry_name=args.registry_name,
        )
        artifacts = validate_metadata(metadata, hashes, args.registry_name)
        shutil.rmtree(args.download_dir, ignore_errors=True)
        download_verified_artifacts(artifacts, args.download_dir, args.request_timeout_seconds)
    except VerificationError as error:
        raise SystemExit(f"REGISTRY_RELEASE_VERIFICATION_FAILED: {error}") from error
    print(
        f"{args.registry_name} release verification passed: "
        f"distribution={DISTRIBUTION_NAME} version={DISTRIBUTION_VERSION} "
        f"artifacts={', '.join(artifact.filename for artifact in artifacts)}"
    )


if __name__ == "__main__":
    main()
