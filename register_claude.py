"""register_claude.py - add the-scout to the Claude desktop app's MCP config.

The app keeps its config in memory and writes it back when it quits, so an edit
made while it runs is lost. This waits until the app has fully quit, backs the
file up, adds (only) the the-scout entry, and exits.

    pythonw register_claude.py        # start it, then quit Claude; reopen Claude after ~10 s
Log: ~/.fetcher/register_claude.log
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

def find_config() -> Path:
    """The desktop app's config file: SCOUT_CLAUDE_CONFIG if set, else the Microsoft Store
    build's (Claude-Data/claude_<id>/), else the standard %APPDATA%/Claude one."""
    override = os.environ.get("SCOUT_CLAUDE_CONFIG")
    if override:
        return Path(override)
    store = sorted((Path.home() / "AppData" / "Local" / "Claude-Data").glob("claude_*/claude_desktop_config.json"))
    if store:
        return store[0]
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Claude" / "claude_desktop_config.json"


CONFIG = find_config()
LOG = Path.home() / ".fetcher" / "register_claude.log"
ENTRY = {"command": str(Path(sys.executable).with_name("python.exe")),
         "args": [str(Path(__file__).resolve().with_name("mcp_server.py"))]}


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def app_running() -> bool:
    """The desktop app (WindowsApps\\Claude_...\\Claude.exe), not the claude CLI."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-Process claude -ErrorAction SilentlyContinue | "
                          "Where-Object { $_.Path -like '*WindowsApps*Claude_*' }).Count"],
                         capture_output=True, text=True, creationflags=0x08000000).stdout.strip()
    return out not in ("", "0")


def main() -> None:
    if not CONFIG.exists():
        log(f"no config at {CONFIG}; set SCOUT_CLAUDE_CONFIG to its path. Nothing changed")
        return
    log("waiting for the Claude app to quit")
    end = time.time() + 30 * 60
    while app_running():
        if time.time() > end:
            log("gave up: the app was still running after 30 minutes; nothing changed")
            return
        time.sleep(2)
    time.sleep(3)                                   # let it finish writing its config
    d = json.loads(CONFIG.read_text(encoding="utf-8"))
    bak = CONFIG.with_name(f"claude_desktop_config.backup-{time.strftime('%Y%m%d-%H%M%S')}.json")
    shutil.copy2(CONFIG, bak)
    d.setdefault("mcpServers", {})["the-scout"] = ENTRY
    CONFIG.write_text(json.dumps(d, indent=2), encoding="utf-8")
    json.loads(CONFIG.read_text(encoding="utf-8"))  # still valid JSON
    log(f"added the-scout (backup {bak.name}); servers now: {list(d['mcpServers'])}. Start Claude.")


if __name__ == "__main__":
    main()
