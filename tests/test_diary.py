import asyncio
from unittest.mock import Mock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from claude_garmin_mcp.diary import Diary
from claude_garmin_mcp.server import create_server


def call(path, name, args, client=None):
    async def run():
        factory = Mock(side_effect=AssertionError("Diary must not contact Garmin"))
        if client is not None:
            factory = lambda: client
        server = create_server(factory, diary=Diary(path))
        async with create_connected_server_and_client_session(server) as session:
            return await session.call_tool(name, args)

    return asyncio.run(run())


def test_diary_lifecycle_persistence_and_garmin_pairing(tmp_path):
    path = tmp_path / "diary.sqlite3"
    original = {
        "occurred_at": "2026-10-08T00:30:00+02:00",
        "bloating": 6,
        "notes": "After lunch; user's report",
    }
    result = call(path, "log_diary_entry", {"entry": original})
    assert not result.isError
    saved = result.structuredContent
    assert saved["pain"] is None
    assert saved["symptom_free"] is False
    assert path.stat().st_mode & 0o777 == 0o600
    query = {"start_date": "2026-10-08", "end_date": "2026-10-08"}
    data = call(path, "get_diary_entries", query).structuredContent
    assert data["entries"] == [saved]
    assert (
        call(
            path,
            "get_diary_entries",
            {"start_date": "2026-10-07", "end_date": "2026-10-07"},
        ).structuredContent["matching_count"]
        == 0
    )
    updated = call(
        path,
        "update_diary_entry",
        {"entry_id": saved["entry_id"], "entry": {**original, "bloating": 4}},
    ).structuredContent
    assert updated["bloating"] == 4
    assert updated["recorded_at"] == saved["recorded_at"]
    client = Mock()
    client.get_stats.return_value = {"totalSteps": 5000}
    client.get_sleep_data.return_value = {"dailySleepDTO": {"sleepTimeSeconds": 25000}}
    client.get_hrv_data.return_value = None
    summary = call(path, "get_daily_summary", {"day": "2026-10-08"}, client)
    assert summary.structuredContent["date"] == data["entries"][0]["occurred_at"][:10]
    client.get_stats.assert_called_once_with("2026-10-08")
    assert call(
        path, "delete_diary_entry", {"entry_id": saved["entry_id"]}
    ).structuredContent["deleted"]
    assert not call(
        path, "delete_diary_entry", {"entry_id": saved["entry_id"]}
    ).structuredContent["deleted"]
    assert call(path, "get_diary_entries", query).structuredContent["entries"] == []


@pytest.mark.parametrize(
    "entry",
    [
        {"occurred_at": "2026-10-08T14:00:00", "pain": 3},
        {"occurred_at": "2026-10-08T14:00:00+02:00", "pain": 11},
        {"occurred_at": "2026-10-08T14:00:00+02:00", "pain": True},
        {"occurred_at": "2026-10-08T14:00:00+02:00", "bristol_type": 8},
        {"occurred_at": "2026-10-08T14:00:00+02:00", "symptom_free": True, "pain": 2},
        {"occurred_at": "2026-10-08T14:00:00+02:00"},
    ],
)
def test_invalid_entry_never_creates_database(tmp_path, entry):
    path = tmp_path / "diary.sqlite3"
    assert call(path, "log_diary_entry", {"entry": entry}).isError
    assert not path.exists()


def test_check_ins_pagination_and_missing_updates(tmp_path):
    path = tmp_path / "diary.sqlite3"
    entry = {"occurred_at": "2026-10-08T14:00:00+02:00", "symptom_free": True}
    for _ in range(2):
        assert not call(path, "log_diary_entry", {"entry": entry}).isError
    query = {"start_date": "2026-10-08", "end_date": "2026-10-08", "limit": 1}
    first = call(path, "get_diary_entries", query).structuredContent
    second = call(
        path, "get_diary_entries", {**query, "offset": first["next_offset"]}
    ).structuredContent
    assert first["matching_count"] == 2
    assert second["next_offset"] is None
    assert first["entries"][0]["entry_id"] != second["entries"][0]["entry_id"]
    assert first["entries"][0]["symptom_free"] is True
    assert call(
        path, "update_diary_entry", {"entry_id": "nonexistent", "entry": entry}
    ).isError
    assert call(path, "get_diary_entries", {**query, "end_date": "2026-10-01"}).isError
