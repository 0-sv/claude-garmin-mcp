"""Interactive authentication is separate from the MCP protocol process."""

import getpass
import logging
import os
from pathlib import Path

from garminconnect import Garmin


def token_dir() -> Path:
    return (
        Path(os.environ.get("GARMIN_TOKEN_DIR", "~/.local/share/claude-garmin-mcp"))
        .expanduser()
        .resolve()
    )


def load_client() -> Garmin:
    directory = token_dir()
    if not (directory / "garmin_tokens.json").is_file():
        raise RuntimeError("Garmin login required. Run: uv run garmin-login")
    client = Garmin(retry_attempts=0)
    client.login(str(directory))
    return client


def main() -> None:
    logging.disable(logging.CRITICAL)
    directory = token_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    try:
        client = Garmin(
            input("Garmin email: ").strip(),
            getpass.getpass("Garmin password: "),
            prompt_mfa=lambda: getpass.getpass("Garmin MFA code: ").strip(),
            retry_attempts=0,
        )
        client.login()
        client.client.dump(str(directory))
        (directory / "garmin_tokens.json").chmod(0o600)
    except (KeyboardInterrupt, EOFError):
        raise SystemExit("Login cancelled.") from None
    except Exception:  # noqa: BLE001 -- Do not expose upstream credentials in errors.
        raise SystemExit(
            "Login failed. Check your credentials/MFA and Garmin availability; "
            "wait before retrying if rate limited."
        ) from None
    print("Garmin session saved. You can now start the MCP server.")
