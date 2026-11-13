import threading
from pathlib import Path

import pytest

from tests.fifo_support import BlockWatchdog


def test_second_start_raises_and_halt_stops_the_only_watcher(tmp_path: Path) -> None:
    before = set(threading.enumerate())
    watchdog = BlockWatchdog(tmp_path / "objeto", seconds=60.0)
    watchdog.start()

    with pytest.raises(RuntimeError, match="já foi ligado"):
        watchdog.start()
    started = set(threading.enumerate()) - before
    watchdog.halt()

    assert len(started) == 1
    assert not any(thread.is_alive() for thread in started)
