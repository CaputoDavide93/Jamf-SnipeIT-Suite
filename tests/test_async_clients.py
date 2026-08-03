"""Failure visibility tests for optional async batch clients."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from clients.async_clients import AsyncJamfClient, AsyncSnipeClient, BatchFetchError


def test_jamf_batch_exposes_failed_ids_and_partial_results():
    client = AsyncJamfClient.__new__(AsyncJamfClient)

    async def get_computer(computer_id):
        if computer_id == 2:
            raise RuntimeError("timeout")
        return {"id": computer_id}

    client.get_computer = get_computer

    with pytest.raises(BatchFetchError) as raised:
        asyncio.run(client.get_computers_batch([1, 2, 3]))

    assert raised.value.failed_ids == [2]
    assert raised.value.partial_results == [{"id": 1}, {"id": 3}]


def test_snipe_batch_returns_complete_successes():
    client = AsyncSnipeClient.__new__(AsyncSnipeClient)

    async def get_asset(asset_id):
        return {"id": asset_id}

    client.get_asset = get_asset

    assert asyncio.run(client.get_assets_batch([4, 5])) == [{"id": 4}, {"id": 5}]
