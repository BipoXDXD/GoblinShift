"""Optional passive evidence; declared bindings and ACKs never verify a host."""

import hashlib
import json
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from .config import Config
from .images import IMAGE_REFERENCE, ImageNotAllowedError, PolicyRead

_SHA256 = re.compile(r"[0-9a-f]{64}")
_TOKEN = re.compile(r"[A-Za-z0-9._:-]{1,160}")
MAX_OBSERVATION_BYTES = 64 * 1024
MAX_ACK_BYTES = 4096


class WitnessError(ImageNotAllowedError):
    """Opt-in evidence was unavailable or invalid, before any executor effect."""


@dataclass(frozen=True)
class InstanceBinding:
    instance_id: str
    process_id: int
    process_birth_id: str
    declared_code_identity: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.instance_id, str)
            or not _TOKEN.fullmatch(self.instance_id)
            or type(self.process_id) is not int
            or self.process_id <= 0
            or not isinstance(self.process_birth_id, str)
            or not _TOKEN.fullmatch(self.process_birth_id)
            or not isinstance(self.declared_code_identity, str)
            or not _SHA256.fullmatch(self.declared_code_identity)
        ):
            raise WitnessError("binding declarado inválido")


@dataclass(frozen=True)
class ReceiptAck:
    body: bytes = field(repr=False)
    sha256: str


class ReceiptSink(Protocol):
    """Operator-supplied synchronous, finite administrative capability.

    No transport, authority, durability, or deadline is discovered/created here.
    The operator must provide those properties; validating an ACK is not proof
    of them. Receipts contain no raw config/policy, ENV, or arbitrary errors.
    """

    def __call__(self, payload: bytes, /) -> ReceiptAck: ...


@dataclass(frozen=True)
class ObservationReceipt:
    observation_sha256: str
    external_receipt_sha256: str
    external_receipt_id: str
    sequence: int
    active_reader_verified: bool = field(default=False, init=False)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate ACK key")
        result[key] = value
    return result


def _ack_id(ack: object, observation_hash: str) -> tuple[str, str]:
    if (
        not isinstance(ack, ReceiptAck)
        or not isinstance(ack.body, bytes)
        or not 0 < len(ack.body) <= MAX_ACK_BYTES
        or not isinstance(ack.sha256, str)
        or not _SHA256.fullmatch(ack.sha256)
        or hashlib.sha256(ack.body).hexdigest() != ack.sha256
    ):
        raise WitnessError("ACK administrativo inválido")
    try:
        data = json.loads(ack.body, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise WitnessError("ACK administrativo inválido") from None
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "observation_sha256", "receipt_id"}
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or data["observation_sha256"] != observation_hash
        or not isinstance(data["receipt_id"], str)
        or not _TOKEN.fullmatch(data["receipt_id"])
    ):
        raise WitnessError("ACK administrativo sem vínculo íntegro")
    return ack.sha256, data["receipt_id"]


class PassiveObserver:
    def __init__(self, binding: InstanceBinding, sink: ReceiptSink) -> None:
        if not isinstance(binding, InstanceBinding) or not callable(sink):
            raise WitnessError("capability de observação inválida")
        self._binding = binding
        self._sink = sink
        self._sequence = 0
        self._lock = threading.Lock()

    def observe(
        self, attempt_id: uuid.UUID, image: str, config: Config, policy: PolicyRead
    ) -> ObservationReceipt:
        if (
            not isinstance(attempt_id, uuid.UUID)
            or not isinstance(image, str)
            or not IMAGE_REFERENCE.fullmatch(image)
            or not isinstance(config.loaded_bytes_sha256, str)
            or not _SHA256.fullmatch(config.loaded_bytes_sha256)
            or not isinstance(policy, PolicyRead)
            or policy.path != str(config.images_file)
            or policy.requested_image_sha256 != hashlib.sha256(image.encode("utf-8")).hexdigest()
        ):
            raise WitnessError("leitura/configuração sem vínculo de observação")
        with self._lock:
            self._sequence += 1
            payload = {
                "schema_version": 1,
                "kind": "executor_policy_read",
                "sequence": self._sequence,
                "observed_at": datetime.now(UTC).isoformat(),
                "declared_instance": asdict(self._binding),
                "binding_provenance": "operator_supplied_not_verified",
                "config_loaded_bytes_sha256": config.loaded_bytes_sha256,
                "runtime": config.runtime,
                "attempt_id": str(attempt_id),
                "requested_image": image,
                "policy": asdict(policy),
                "parse": "valid",
                "membership": "allowed",
                "executor_effect": "not_attempted",
                "active_reader_verified": False,
            }
            raw = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
            ).encode("utf-8")
            if len(raw) > MAX_OBSERVATION_BYTES:
                raise WitnessError("observação acima do limite")
            digest = hashlib.sha256(raw).hexdigest()
            try:
                ack = self._sink(raw)
            except Exception:
                raise WitnessError("capability administrativa indisponível") from None
            external_hash, external_id = _ack_id(ack, digest)
            return ObservationReceipt(digest, external_hash, external_id, self._sequence)
