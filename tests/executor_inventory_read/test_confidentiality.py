"""Only the existing HTTP error mapping is called; no HTTP socket is opened."""

import importlib
import json
import logging
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from executor_ctl import service

from .test_snapshot import A, policy
from .test_witness import ATTEMPT, DockerBoundary, binding, cfg, witness_module


def test_sink_exception_is_closed_at_existing_http_error_boundary(
    tmp_path: Path,
    root_metadata_boundary: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = importlib.import_module("executor_ctl.server")
    w = witness_module()
    path = policy(tmp_path)
    docker = DockerBoundary()

    def sink(raw: bytes) -> object:
        raise RuntimeError("SYNTHETIC_SECRET_NEVER_JOURNAL")

    with caplog.at_level(logging.DEBUG), pytest.raises(w.WitnessError) as caught:
        service.ExecutorService(
            replace(cfg(path), loaded_bytes_sha256="d" * 64),
            docker,
            7,
            observer=w.PassiveObserver(binding(), sink),
        ).start(ATTEMPT, A)
    status, payload = server._error_payload(caught.value)
    assert int(status) == 403
    assert payload["error"] == "image_not_allowed"
    assert "SYNTHETIC_SECRET_NEVER_JOURNAL" not in json.dumps(payload)
    assert "SYNTHETIC_SECRET_NEVER_JOURNAL" not in caplog.text
    assert docker.calls == []


def test_provider_exception_never_echoes_arbitrary_error(
    tmp_path: Path,
    root_metadata_boundary: object,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    main = importlib.import_module("executor_ctl.__main__")
    path = policy(tmp_path)
    monkeypatch.setattr(
        main, "load_config", lambda config_path: replace(cfg(path), loaded_bytes_sha256="d" * 64)
    )
    # No logging configuration, group lookup or Docker call reaches the host.
    monkeypatch.setattr(main, "configure_logging", lambda: None)
    monkeypatch.setattr(main.grp, "getgrnam", lambda group: SimpleNamespace(gr_gid=7))

    def provider(config: object) -> object:
        raise RuntimeError("SYNTHETIC_PROVIDER_SECRET_NEVER_JOURNAL")

    with caplog.at_level(logging.DEBUG):
        result = main.main(["synthetic-config"], observer_provider=provider)
    assert result == main.EXIT_HOST_NOT_READY
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    assert "SYNTHETIC_PROVIDER_SECRET_NEVER_JOURNAL" not in caplog.text
