"""Local symptom diary, independent of Garmin authentication."""

import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from mcp.types import ToolAnnotations
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Score = Annotated[int, Field(strict=True, ge=0, le=10)]


class DiaryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurred_at: AwareDatetime = Field(
        description="When symptoms/check-in occurred, with the user's local UTC offset."
    )
    symptom_free: bool = False
    pain: Score | None = None
    bloating: Score | None = None
    nausea: Score | None = None
    urgency: Score | None = None
    bristol_type: Annotated[int, Field(strict=True, ge=1, le=7)] | None = None
    perceived_stress: Score | None = None
    notes: Annotated[str, Field(max_length=4000)] | None = None

    @model_validator(mode="after")
    def validate_observation(self):
        symptoms = (self.pain, self.bloating, self.nausea, self.urgency)
        if self.symptom_free and any(
            value is not None and value > 0 for value in symptoms
        ):
            raise ValueError(
                "A symptom-free check-in cannot have positive symptom scores."
            )
        if (
            not self.symptom_free
            and all(value is None for value in symptoms)
            and (
                self.bristol_type is None
                and self.perceived_stress is None
                and not (self.notes and self.notes.strip())
            )
        ):
            raise ValueError("Provide an observation or explicitly mark symptom_free.")
        return self


def default_path() -> Path:
    return (
        Path(
            os.environ.get(
                "GARMIN_DIARY_PATH", "~/.local/share/claude-garmin-mcp/diary.sqlite3"
            )
        )
        .expanduser()
        .resolve()
    )


class Diary:
    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else default_path()

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY, local_date TEXT NOT NULL,
            recorded_at TEXT NOT NULL, updated_at TEXT NOT NULL, data TEXT NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS entries_date ON entries(local_date)")
        conn.commit()
        return conn

    @staticmethod
    def decode(row):
        return {
            "entry_id": row["id"],
            "recorded_at": row["recorded_at"],
            "updated_at": row["updated_at"],
            **json.loads(row["data"]),
        }

    def save(self, entry: DiaryEntry, entry_id: str | None = None):
        now = datetime.now(UTC).isoformat()
        with closing(self.connect()) as conn, conn:
            values = (
                entry.occurred_at.date().isoformat(),
                now,
                entry.model_dump_json(),
            )
            if entry_id is None:
                entry_id = str(uuid4())
                conn.execute(
                    "INSERT INTO entries(id, recorded_at, local_date, updated_at, data) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (entry_id, now, *values),
                )
            else:
                result = conn.execute(
                    "UPDATE entries SET local_date=?, updated_at=?, data=? WHERE id=?",
                    (*values, entry_id),
                )
                if result.rowcount == 0:
                    raise ValueError("Diary entry not found.")
            row = conn.execute(
                "SELECT * FROM entries WHERE id=?", (entry_id,)
            ).fetchone()
            return self.decode(row)

    def list(self, start: date, end: date, limit: int, offset: int):
        if not 0 <= (end - start).days <= 365:
            raise ValueError("Choose an ordered date range of at most 366 days.")
        with closing(self.connect()) as conn:
            params = (start.isoformat(), end.isoformat())
            count = conn.execute(
                "SELECT count(*) FROM entries WHERE local_date BETWEEN ? AND ?", params
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM entries WHERE local_date BETWEEN ? AND ? "
                "ORDER BY local_date, recorded_at, id LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "matching_count": count,
            "entries": [self.decode(row) for row in rows],
            "next_offset": offset + len(rows) if offset + len(rows) < count else None,
            "missing_days_mean": "not recorded, not symptom-free",
        }

    def delete(self, entry_id: str):
        with closing(self.connect()) as conn, conn:
            result = conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        return {"entry_id": entry_id, "deleted": result.rowcount > 0}


def register_diary_tools(server, diary: Diary):
    local_read = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    local_create = ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
    local_edit = ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    )
    local_delete = ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    )

    @server.tool(annotations=local_create)
    def log_diary_entry(entry: DiaryEntry) -> dict[str, Any]:
        """Save a user-reported symptom episode or explicit symptom-free check-in locally.

        Scores are 0–10, Bristol stool type 1–7. Omitted values stay unknown.
        Ask for missing symptom time/offset rather than inventing them. Use the
        user's Garmin-local offset so occurred_at's date aligns with daily health.
        Put optional meals, caffeine/alcohol, medication changes in notes.
        Save only when the user asks/agrees to log; do not infer diary facts from
        Garmin scores. Returns the saved entry ID. Does not contact Garmin.
        """
        return diary.save(entry)

    @server.tool(annotations=local_read)
    def get_diary_entries(
        start_date: date,
        end_date: date,
        limit: Annotated[int, Field(ge=1, le=200)] = 100,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        """Read local diary entries by inclusive occurrence date (max 366 days).

        Follow next_offset to fetch remaining entries before making comparisons.
        Dates use each entry's reported local offset. Empty days are unrecorded.
        Pair with get_daily_summary for the same Garmin-local date. Read today's
        entries before suggesting a daily check-in during a symptom conversation.
        """
        return diary.list(start_date, end_date, limit, offset)

    @server.tool(annotations=local_edit)
    def update_diary_entry(entry_id: str, entry: DiaryEntry) -> dict[str, Any]:
        """Replace an existing local entry with corrected values, preserving recorded_at.

        Read the entry first and include ALL fields to retain. Omitted optional
        values are cleared. Only make corrections requested by the user.
        """
        return diary.save(entry, entry_id)

    @server.tool(annotations=local_delete)
    def delete_diary_entry(entry_id: str) -> dict[str, Any]:
        """Delete one local diary entry when requested by the user. Cannot be undone.

        Returns deleted=false if the ID no longer exists. Garmin data is untouched.
        """
        return diary.delete(entry_id)
