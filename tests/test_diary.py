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


@pytest.mark.parametrize(
    "intake",
    [
        {"food": [{"name": "Pasta", "amount": "one bowl"}]},
        {
            "alcohol": [
                {
                    "name": "Beer",
                    "amount": "two bottles",
                    "volume_ml": 660,
                    "abv_percent": 5,
                }
            ]
        },
        {"caffeine": [{"name": "Coffee", "amount": "one mug"}]},
        {"caffeine": [{"name": "Energy drink", "caffeine_mg": 80}]},
    ],
)
def test_intake_only_persists_and_can_be_corrected(tmp_path, intake):
    path = tmp_path / "diary.sqlite3"
    entry = {"occurred_at": "2026-10-08T14:00:00+02:00", **intake}
    result = call(path, "log_diary_entry", {"entry": entry})
    assert not result.isError
    saved = result.structuredContent
    assert saved["pain"] is None
    assert saved["symptom_free"] is False
    for field, items in intake.items():
        for key, value in items[0].items():
            assert saved[field][0][key] == value
    if "caffeine" in intake and "caffeine_mg" not in intake["caffeine"][0]:
        assert saved["caffeine"][0]["caffeine_mg"] is None
    loaded = call(
        path,
        "get_diary_entries",
        {"start_date": "2026-10-08", "end_date": "2026-10-08"},
    ).structuredContent["entries"][0]
    assert loaded == saved
    corrected = call(
        path,
        "update_diary_entry",
        {
            "entry_id": saved["entry_id"],
            "entry": {**entry, "notes": "Corrected context"},
        },
    ).structuredContent
    for field in intake:
        assert corrected[field] == saved[field]


@pytest.mark.parametrize(
    "intake",
    [
        {"food": [{"name": "   "}]},
        {"food": []},
        {"alcohol": [{"name": "Beer", "volume_ml": -1}]},
        {"alcohol": [{"name": "Beer", "abv_percent": 101}]},
        {"caffeine": [{"name": "Coffee", "caffeine_mg": -1}]},
        {"caffeine": [{"name": "Coffee", "caffeine_mg": True}]},
    ],
)
def test_invalid_intake_is_rejected_without_writes(tmp_path, intake):
    path = tmp_path / "diary.sqlite3"
    result = call(
        path,
        "log_diary_entry",
        {"entry": {"occurred_at": "2026-10-08T14:00:00+02:00", **intake}},
    )
    assert result.isError
    assert not path.exists()


def test_old_entries_read_with_unknown_intake():
    import json

    row = {
        "id": "old",
        "recorded_at": "original",
        "updated_at": "original",
        "data": json.dumps({"notes": "Coffee after lunch", "pain": 2}),
    }
    decoded = Diary.decode(row)
    assert decoded["notes"] == "Coffee after lunch"
    assert decoded["food"] is None
    assert decoded["alcohol"] is None
    assert decoded["caffeine"] is None
