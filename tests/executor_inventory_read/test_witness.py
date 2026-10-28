import hashlib
import importlib
import json
import uuid
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from executor_ctl import config, images, service
from executor_ctl.docker_cli import ContainerStatus, DockerCli
from executor_ctl.witness import (
    InstanceBinding,
    ObservationReceipt,
    PassiveObserver,
    ReceiptAck,
    ReceiptSink,
)

from .conftest import MetadataBoundary
from .test_snapshot import A, B, policy

ATTEMPT = uuid.UUID("11111111-1111-4111-8111-111111111111")


def witness_module() -> ModuleType:
    return importlib.import_module("executor_ctl.witness")


def binding() -> InstanceBinding:
    return InstanceBinding("operator-instance", 1234, "operator-birth", "c" * 64)


def cfg(path: Path) -> config.Config:
    return config.Config(path.parent / "socket", "test", path.parent / "work", "runsc", path, False)


class DockerBoundary(DockerCli):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def inspect(self, name: str) -> ContainerStatus:
        self.calls.append(("inspect", name))
        return ContainerStatus("running", None, False, None, None)

    def run(self, arguments: list[str]) -> None:
        pytest.fail("unexpected Docker run boundary")

    def stop(self, name: str, grace_s: int) -> None:
        pytest.fail("unexpected Docker stop boundary")

    def remove(self, name: str) -> None:
        pytest.fail("unexpected Docker remove boundary")


def good_sink(observations: list[bytes]) -> ReceiptSink:
    def sink(raw: bytes) -> ReceiptAck:
        observations.append(raw)
        ack = json.dumps(
            {
                "schema_version": 1,
                "observation_sha256": hashlib.sha256(raw).hexdigest(),
                "receipt_id": "admin-receipt",
            }
        ).encode()
        return ReceiptAck(ack, hashlib.sha256(ack).hexdigest())

    return sink


def test_default_service_preserves_existing_start_without_observation(
    tmp_path: Path, root_metadata_boundary: MetadataBoundary
) -> None:
    path = policy(tmp_path)
    docker = DockerBoundary()
    assert service.ExecutorService(cfg(path), docker, 7).start(ATTEMPT, A).state == "running"
    assert len(docker.calls) == 1


def test_observer_uses_same_check_and_ack_before_docker(
    tmp_path: Path, root_metadata_boundary: MetadataBoundary
) -> None:
    w = witness_module()
    path = policy(tmp_path)
    observations: list[bytes] = []
    docker = DockerBoundary()
    loaded = replace(cfg(path), loaded_bytes_sha256="d" * 64)

    def sink(raw: bytes) -> ReceiptAck:
        assert docker.calls == []
        # Replacement after the policy decision must not be attributed to that decision.
        path.write_bytes(json.dumps({"allowed": [B]}).encode())
        return good_sink(observations)(raw)

    observer = w.PassiveObserver(binding(), sink)
    service.ExecutorService(loaded, docker, 7, observer=observer).start(ATTEMPT, A)
    record = json.loads(observations[0])
    assert record["membership"] == "allowed"
    assert record["parse"] == "valid" and record["executor_effect"] == "not_attempted"
    assert record["active_reader_verified"] is False
    assert record["policy"]["bytes_sha256"] != hashlib.sha256(path.read_bytes()).hexdigest()
    assert record["config_loaded_bytes_sha256"] == "d" * 64
    assert record["requested_image"] == A and record["attempt_id"] == str(ATTEMPT)
    assert record["declared_instance"]["process_id"] == 1234
    assert len(docker.calls) == 1


@pytest.mark.parametrize(
    "failure", ["raises", "wrong_hash", "wrong_binding", "invalid_json", "wrong_type"]
)
def test_optin_bad_ack_blocks_before_executor_effects(
    tmp_path: Path, root_metadata_boundary: MetadataBoundary, failure: str
) -> None:
    w = witness_module()
    path = policy(tmp_path)
    docker = DockerBoundary()

    def sink(raw: bytes) -> object:
        if failure == "raises":
            raise RuntimeError("secret-never-echo")
        if failure == "wrong_type":
            return True
        ack = json.dumps(
            {
                "schema_version": 1,
                "observation_sha256": hashlib.sha256(raw).hexdigest(),
                "receipt_id": "test",
            }
        ).encode()
        if failure == "wrong_binding":
            ack = ack.replace(hashlib.sha256(raw).hexdigest().encode(), b"0" * 64)
        if failure == "invalid_json":
            ack = b'{"schema_version":1,"schema_version":1}'
        return ReceiptAck(
            ack, "0" * 64 if failure == "wrong_hash" else hashlib.sha256(ack).hexdigest()
        )

    loaded = replace(cfg(path), loaded_bytes_sha256="d" * 64)
    with pytest.raises(w.WitnessError) as error:
        service.ExecutorService(
            loaded, docker, 7, observer=w.PassiveObserver(binding(), sink)
        ).start(ATTEMPT, A)
    assert "secret-never-echo" not in str(error.value)
    assert docker.calls == []


def test_missing_loaded_config_hash_cannot_emit_witness(
    tmp_path: Path, root_metadata_boundary: MetadataBoundary
) -> None:
    w = witness_module()
    path = policy(tmp_path)
    docker = DockerBoundary()
    seen: list[bytes] = []
    with pytest.raises(w.WitnessError):
        service.ExecutorService(
            cfg(path), docker, 7, observer=w.PassiveObserver(binding(), good_sink(seen))
        ).start(ATTEMPT, A)
    assert seen == [] and docker.calls == []


def test_denied_membership_does_not_emit_or_touch_executor(
    tmp_path: Path, root_metadata_boundary: MetadataBoundary
) -> None:
    w = witness_module()
    path = policy(tmp_path, json.dumps({"allowed": [A]}).encode())
    docker = DockerBoundary()
    seen: list[bytes] = []
    loaded = replace(cfg(path), loaded_bytes_sha256="d" * 64)
    with pytest.raises(images.ImageNotAllowedError):
        service.ExecutorService(
            loaded, docker, 7, observer=w.PassiveObserver(binding(), good_sink(seen))
        ).start(ATTEMPT, B)
    assert seen == [] and docker.calls == []


def test_observation_receipt_is_explicitly_not_host_verification(
    tmp_path: Path, root_metadata_boundary: MetadataBoundary
) -> None:
    w = witness_module()
    path = policy(tmp_path)
    seen: list[bytes] = []
    result = w.PassiveObserver(binding(), good_sink(seen)).observe(
        ATTEMPT,
        A,
        replace(cfg(path), loaded_bytes_sha256="d" * 64),
        images.require_allowed(path, A),
    )
    assert result.active_reader_verified is False
    assert result.observation_sha256 == hashlib.sha256(seen[0]).hexdigest()
    assert len(result.external_receipt_sha256) == 64


@pytest.mark.parametrize(
    "override",
    [
        {"instance_id": ""},
        {"process_id": True},
        {"process_id": 0},
        {"process_birth_id": "bad\nvalue"},
        {"declared_code_identity": "not-loaded-code"},
    ],
)
def test_invalid_declared_binding_is_rejected(override: dict[str, object]) -> None:
    w = witness_module()
    values = {
        "instance_id": "operator-instance",
        "process_id": 1234,
        "process_birth_id": "birth",
        "declared_code_identity": "c" * 64,
    }
    values.update(override)
    with pytest.raises(w.WitnessError):
        w.InstanceBinding(**values)


def test_snapshot_cannot_be_rebound_to_different_requested_image(
    tmp_path: Path, root_metadata_boundary: MetadataBoundary
) -> None:
    w = witness_module()
    path = policy(tmp_path)
    seen: list[bytes] = []
    snapshot = images.require_allowed(path, A)
    with pytest.raises(w.WitnessError):
        w.PassiveObserver(binding(), good_sink(seen)).observe(
            ATTEMPT, B, replace(cfg(path), loaded_bytes_sha256="d" * 64), snapshot
        )
    assert seen == []


@pytest.mark.parametrize(
    "data",
    [
        {"schema_version": True, "receipt_id": "test"},
        {"schema_version": 2, "receipt_id": "test"},
        {"schema_version": 1, "receipt_id": "test", "extra": "no"},
        {"schema_version": 1, "receipt_id": "bad\nvalue"},
    ],
)
def test_ack_schema_is_closed_and_bounded(
    tmp_path: Path, root_metadata_boundary: MetadataBoundary, data: dict[str, object]
) -> None:
    w = witness_module()
    path = policy(tmp_path)

    def sink(raw: bytes) -> ReceiptAck:
        ack = json.dumps({**data, "observation_sha256": hashlib.sha256(raw).hexdigest()}).encode()
        return ReceiptAck(ack, hashlib.sha256(ack).hexdigest())

    with pytest.raises(w.WitnessError):
        w.PassiveObserver(binding(), sink).observe(
            ATTEMPT,
            A,
            replace(cfg(path), loaded_bytes_sha256="d" * 64),
            images.require_allowed(path, A),
        )


def test_concurrent_observations_have_unique_sequence_and_matching_ack(
    tmp_path: Path, root_metadata_boundary: MetadataBoundary
) -> None:
    import threading

    w = witness_module()
    path = policy(tmp_path)
    seen: list[bytes] = []
    results: list[ObservationReceipt] = []
    errors: list[Exception] = []
    observer = w.PassiveObserver(binding(), good_sink(seen))
    loaded = replace(cfg(path), loaded_bytes_sha256="d" * 64)

    def observe() -> None:
        try:
            results.append(observer.observe(ATTEMPT, A, loaded, images.require_allowed(path, A)))
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=observe) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert errors == [] and sorted(r.sequence for r in results) == [1, 2, 3, 4]
    assert len({r.observation_sha256 for r in results}) == 4
    assert [json.loads(raw)["sequence"] for raw in seen] == [1, 2, 3, 4]


def test_optional_main_provider_wires_existing_start_without_cli_extension(
    tmp_path: Path, root_metadata_boundary: MetadataBoundary, monkeypatch: pytest.MonkeyPatch
) -> None:
    main = importlib.import_module("executor_ctl.__main__")
    path = policy(tmp_path)
    seen: list[bytes] = []
    loaded = replace(cfg(path), loaded_bytes_sha256="d" * 64)
    docker = DockerBoundary()
    received: list[config.Config] = []
    monkeypatch.setattr(main, "load_config", lambda path: loaded)
    monkeypatch.setattr(main, "configure_logging", lambda: None)
    monkeypatch.setattr(main.grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=7))
    monkeypatch.setattr(main, "DockerCli", lambda: docker)
    monkeypatch.setattr(main.signal, "signal", lambda *args: None)

    class Server:
        def __init__(self, service: service.ExecutorService) -> None:
            self.service = service

        def serve_forever(self) -> None:
            self.service.start(ATTEMPT, A)

        def server_close(self) -> None:
            pass

    monkeypatch.setattr(main, "bind_server", lambda config, service, gid: Server(service))

    def provider(actual: config.Config) -> PassiveObserver | None:
        received.append(actual)
        return PassiveObserver(binding(), good_sink(seen))

    assert main.main(["synthetic-config-path"], observer_provider=provider) == 0
    assert received == [loaded] and len(seen) == 1 and len(docker.calls) == 1


@pytest.mark.parametrize("failure", ["missing", "raises"])
def test_optin_main_provider_failure_does_not_construct_executor(
    tmp_path: Path,
    root_metadata_boundary: MetadataBoundary,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    main = importlib.import_module("executor_ctl.__main__")
    w = witness_module()
    path = policy(tmp_path)
    monkeypatch.setattr(
        main, "load_config", lambda config_path: replace(cfg(path), loaded_bytes_sha256="d" * 64)
    )
    monkeypatch.setattr(main, "configure_logging", lambda: None)
    monkeypatch.setattr(main.grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=7))

    def never() -> None:
        pytest.fail("executor must not be constructed")

    monkeypatch.setattr(main, "DockerCli", never)

    def provider(config: config.Config) -> PassiveObserver | None:
        if failure == "raises":
            raise w.WitnessError("closed error")
        return None

    assert (
        main.main(["synthetic-config-path"], observer_provider=provider) == main.EXIT_HOST_NOT_READY
    )
