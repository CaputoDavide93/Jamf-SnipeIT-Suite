"""Persistence safety tests for scheduler state and retry metadata."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.state import RetryQueue, SyncState


def test_sync_state_writes_valid_json_atomically(tmp_path):
    path = tmp_path / "sync_state.json"
    state = SyncState(str(path))

    state.set("module", "cursor", {"offset": 10})

    assert json.loads(path.read_text()) == {"module": {"cursor": {"offset": 10}}}
    assert list(tmp_path.glob(".sync_state.json.*")) == []


def test_corrupt_sync_state_is_preserved(tmp_path):
    path = tmp_path / "sync_state.json"
    path.write_text("{broken")

    state = SyncState(str(path))

    assert state.all() == {}
    backups = list(tmp_path.glob("sync_state.json.corrupt-*") )
    assert len(backups) == 1
    assert backups[0].read_text() == "{broken"
    assert not path.exists()


def test_retry_queue_uses_atomic_storage_and_preserves_corruption(tmp_path):
    path = tmp_path / "retry_queue.json"
    queue = RetryQueue(str(path))
    item_id = queue.add("user_match", "checkout", {"asset": 1}, "timeout")

    stored = json.loads(path.read_text())
    assert stored[0]["id"] == item_id
    assert list(tmp_path.glob(".retry_queue.json.*")) == []

    path.write_text("not-json")
    recovered = RetryQueue(str(path))
    assert recovered.get_pending() == []
    assert len(list(tmp_path.glob("retry_queue.json.corrupt-*"))) == 1
