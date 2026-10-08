import asyncio
import os
from unittest.mock import Mock

import pytest
from garminconnect import GarminConnectTooManyRequestsError
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

from claude_garmin_mcp.server import create_server


def call(client, tool, arguments):
    async def run():
        async with create_connected_server_and_client_session(
            create_server(lambda: client)
        ) as session:
            return await session.call_tool(tool, arguments)

    return asyncio.run(run())


def test_daily_summary_preserves_missing_values_and_partial_failures():
    client = Mock()
    client.get_stats.return_value = {"totalSteps": 0, "restingHeartRate": None}
    client.get_sleep_data.return_value = {
        "dailySleepDTO": {"sleepTimeSeconds": 28800},
        "sleepMovement": ["large time series"],
    }
    client.get_hrv_data.side_effect = GarminConnectTooManyRequestsError("secret token")
    result = call(client, "get_daily_summary", {"day": "2026-10-07"})
    assert not result.isError
    data = result.structuredContent
    assert data["daily"] == {"totalSteps": 0, "restingHeartRate": None}
    assert data["sleep"] == {"sleepTimeSeconds": 28800}
    assert data["hrv"] is None
    assert "rate limited" in data["errors"]["hrv"]
    assert "secret token" not in str(result)
    client.get_stats.assert_called_once_with("2026-10-07")


def test_list_marks_truncation_and_details_uses_id():
    client = Mock()
    client.get_activities_by_date.return_value = [
        {"activityId": 123, "distance": 5000, "ownerFullName": "private"},
        {"activityId": 122, "distance": 10000},
    ]
    result = call(
        client,
        "list_activities",
        {
            "start_date": "2026-10-01",
            "end_date": "2026-10-07",
            "limit": 1,
            "activity_type": "running",
        },
    )
    assert not result.isError
    assert result.structuredContent["truncated"] is True
    assert result.structuredContent["matching_count"] == 2
    assert result.structuredContent["activities"] == [
        {"activityId": 123, "distance": 5000}
    ]
    client.get_activities_by_date.assert_called_once_with(
        "2026-10-01", "2026-10-07", "running"
    )
    client.get_activity.return_value = {
        "activityId": 123,
        "summaryDTO": {"distance": 5000},
    }
    result = call(client, "get_activity_details", {"activity_id": "123"})
    assert result.structuredContent == client.get_activity.return_value
    client.get_activity.assert_called_once_with("123")


@pytest.mark.parametrize(
    "tool,args",
    [
        ("get_daily_summary", {"day": "2026-02-30"}),
        ("list_activities", {"start_date": "2026-10-08", "end_date": "2026-10-01"}),
        ("list_activities", {"start_date": "2026-01-01", "end_date": "2026-10-01"}),
        (
            "list_activities",
            {"start_date": "2026-10-01", "end_date": "2026-10-08", "limit": 101},
        ),
        ("get_activity_details", {"activity_id": "../invalid"}),
    ],
)
def test_invalid_inputs_never_reach_garmin(tool, args):
    client = Mock()
    assert call(client, tool, args).isError
    assert not client.mock_calls


def test_upstream_errors_are_sanitized():
    client = Mock()
    client.get_activity.side_effect = Exception("password=secret")
    result = call(client, "get_activity_details", {"activity_id": "123"})
    assert result.isError
    assert "secret" not in str(result)
    assert "Garmin request failed" in str(result)


def test_real_stdio_launch_lists_tools_and_explains_missing_login(tmp_path):
    async def run():
        params = StdioServerParameters(
            command="uv",
            args=["run", "--frozen", "garmin-mcp"],
            env={**os.environ, "GARMIN_TOKEN_DIR": str(tmp_path)},
        )
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = (await session.list_tools()).tools
            assert {tool.name for tool in tools} == {
                "get_daily_summary",
                "list_activities",
                "get_activity_details",
                "log_diary_entry",
                "get_diary_entries",
                "update_diary_entry",
                "delete_diary_entry",
            }
            writable = {"log_diary_entry", "update_diary_entry", "delete_diary_entry"}
            assert all(
                tool.annotations.readOnlyHint == (tool.name not in writable)
                for tool in tools
            )
            result = await session.call_tool(
                "get_activity_details", {"activity_id": "123"}
            )
            assert result.isError
            assert "uv run garmin-login" in str(result)

    asyncio.run(run())
