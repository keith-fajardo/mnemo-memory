import base64
import hashlib
import json
import urllib.error
from email.message import Message

import pytest

from scripts.verify_testpypi_release import (
    DISTRIBUTION_NAME,
    DISTRIBUTION_VERSION,
    EXPECTED_FILENAMES,
    PYPI_PUBLISH_PREDICATE,
    UploadedArtifact,
    VerificationError,
    poll_metadata,
    validate_metadata,
    validate_provenance,
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


def provenance(
    artifact: UploadedArtifact,
    *,
    repository: str = "keith-fajardo/mnemo-memory",
    workflow: str = "publish-testpypi.yml",
    digest: str | None = None,
) -> dict[str, object]:
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [
            {
                "name": artifact.filename,
                "digest": {"sha256": digest or artifact.sha256},
            }
        ],
        "predicateType": PYPI_PUBLISH_PREDICATE,
        "predicate": None,
    }
    encoded = base64.b64encode(
        json.dumps(statement, sort_keys=True, separators=(",", ":")).encode()
    ).decode()
    return {
        "version": 1,
        "attestation_bundles": [
            {
                "publisher": {
                    "kind": "GitHub",
                    "repository": repository,
                    "workflow": workflow,
                },
                "attestations": [
                    {
                        "version": 1,
                        "envelope": {"signature": "signed", "statement": encoded},
                        "verification_material": {
                            "certificate": "certificate",
                            "transparency_entries": [{}],
                        },
                    }
                ],
            }
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


def test_registry_provenance_is_bound_to_artifact_and_trusted_workflow() -> None:
    artifact = UploadedArtifact("package.whl", "a" * 64, "https://files.test/package.whl")

    validate_provenance(
        provenance(artifact),
        artifact,
        "keith-fajardo/mnemo-memory",
        "publish-testpypi.yml",
    )
    with pytest.raises(VerificationError, match="exactly one expected"):
        validate_provenance(
            provenance(artifact, repository="attacker/fork"),
            artifact,
            "keith-fajardo/mnemo-memory",
            "publish-testpypi.yml",
        )
    with pytest.raises(VerificationError, match="subject does not match"):
        validate_provenance(
            provenance(artifact, digest="b" * 64),
            artifact,
            "keith-fajardo/mnemo-memory",
            "publish-testpypi.yml",
        )


def test_registry_provenance_requires_signature_certificate_and_transparency() -> None:
    artifact = UploadedArtifact("package.whl", "a" * 64, "https://files.test/package.whl")
    value = provenance(artifact)
    bundles = value["attestation_bundles"]
    assert isinstance(bundles, list)
    bundle = bundles[0]
    assert isinstance(bundle, dict)
    attestations = bundle["attestations"]
    assert isinstance(attestations, list)
    attestation = attestations[0]
    assert isinstance(attestation, dict)
    material = attestation["verification_material"]
    assert isinstance(material, dict)
    material["transparency_entries"] = []

    with pytest.raises(VerificationError, match="signed verification material"):
        validate_provenance(
            value,
            artifact,
            "keith-fajardo/mnemo-memory",
            "publish-testpypi.yml",
        )
