"""telegram_bot.py - Phase 6: The Scout on Telegram, for you only.

    python telegram_bot.py whoami     # once: prints the user ID of whoever messages the bot
    python telegram_bot.py            # run the bot (long polling)
    python telegram_bot.py install-startup / remove-startup   # start with Windows

.env (never in chat or code):
    TELEGRAM_BOT_TOKEN=...            from @BotFather
    TELEGRAM_ALLOWED_USER_ID=...      your numeric ID (from `whoami`)

Messages:
    any text          research it with Claude (PLAN §2: normal message -> Claude)
    /local <question> research it with the local GPU model
    /stop             stop the current run here (Claude chat keeps going)
    /stopall          stop everything: this bot, Claude chat and n8n
    /report           send the last answer as a Markdown report file
    /help

Only messages from TELEGRAM_ALLOWED_USER_ID in a private chat are read (PLAN §5:
a bot that drives your browser is remote control of your accounts). Anything else
gets no reply and is logged. Stdlib only: no Telegram library needed.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

import act
import scout

LOG_PATH = Path.home() / ".fetcher" / "telegram.log"
SURFACE = "telegram"                             # its own stop flag (act.py)
HELP = ("Ask me anything and I'll research it (Claude brain).\n"
        "/local <question> - use the local GPU model instead\n"
        "/stop - stop the current run\n"
        "/stopall - stop everything, Claude chat and n8n too\n"
        "/report - get the last answer as a Markdown file")
CHUNK = 4000                                     # Telegram's limit is 4096


class TelegramAPI:
    def __init__(self, token: str) -> None:
        self.base = f"https://api.telegram.org/bot{token}/"

    def call(self, method: str, payload: Optional[dict] = None, timeout: float = 40.0):
        req = urllib.request.Request(self.base + method, data=json.dumps(payload or {}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        if not d.get("ok"):
            raise RuntimeError(f"Telegram {method}: {d.get('description')}")
        return d["result"]

    def get_updates(self, offset: Optional[int], timeout: int = 30) -> list:
        return self.call("getUpdates", {"offset": offset, "timeout": timeout, "allowed_updates": ["message"]},
                         timeout=timeout + 10)

    def send_message(self, chat_id: int, text: str) -> None:
        self.call("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})

    def send_document(self, chat_id: int, path: Path | str) -> None:
        path = Path(path)
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{path.name}\"\r\n"
                f"Content-Type: text/markdown\r\n\r\n").encode("utf-8") + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(self.base + "sendDocument", data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            if not json.loads(resp.read().decode("utf-8")).get("ok"):
                raise RuntimeError("Telegram sendDocument failed")


def split_message(text: str, size: int = CHUNK) -> list[str]:
    """Telegram-sized pieces, cut at line breaks where possible."""
    out = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        cut = cut if cut > size // 2 else size
        out.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return out + ([text] if text else [])


class Bot:
    def __init__(self, api, allowed_user_id: Optional[int], runner: Callable = scout.run_research,
                 log_path: Path | str = LOG_PATH, reports_dir: Path | str = scout.REPORTS_DIR,
                 ping_every: float = 20.0) -> None:
        if not allowed_user_id:
            raise ValueError("TELEGRAM_ALLOWED_USER_ID is not set: the bot only ever answers one person")
        self.api = api
        self.allowed = int(allowed_user_id)
        self.runner = runner
        self.log_path = Path(log_path)
        self.reports_dir = Path(reports_dir)
        self.ping_every = ping_every
        self.last_run = None
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scout-telegram")
        self._busy = threading.Lock()
        self._current: Optional[str] = None
        self._futures: list = []
        self._last_ping = 0.0

    def _log(self, line: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")

    def _say(self, text: str) -> None:
        for part in split_message(text):
            self.api.send_message(self.allowed, part)

    # -- one update -------------------------------------------------------------------

    def handle(self, update: dict) -> None:
        m = update.get("message") or {}
        user = (m.get("from") or {}).get("id")
        chat = (m.get("chat") or {}).get("id")
        if user != self.allowed or chat != self.allowed:
            self._log(f"ignored: user {user} chat {chat}")           # no reply, ever
            return
        text = (m.get("text") or "").strip()
        if not text:
            return
        cmd, _, rest = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        if cmd in ("/start", "/help"):
            self._say(HELP)
        elif cmd == "/stop":
            act.stop(SURFACE)
            self._say("Stopping. The current run ends after the page batch it's reading.")
        elif cmd == "/stopall":
            act.stop_all()
            self._say("Stopping everything, Claude chat and n8n too. Your next message there or here starts fresh.")
        elif cmd == "/report":
            self._report()
        elif cmd == "/local":
            self._start(rest.strip(), "local")
        elif cmd.startswith("/"):
            self._say("Unknown command.\n" + HELP)
        else:
            self._start(text, "claude")

    def _start(self, question: str, brain: str) -> None:
        if not question:
            self._say("Put the question after the command, e.g. /local what is n8n?")
            return
        if not self._busy.acquire(blocking=False):
            self._say(f"Still working on: {self._current!r}. Send /stop to cancel it first.")
            return
        self._current = question
        self._log(f"question ({brain}): {question[:200]}")
        self._say(f"On it ({'local model' if brain == 'local' else 'Claude'}). Send /stop to cancel.")
        self._futures.append(self._worker.submit(self._run, question, brain))

    def _run(self, question: str, brain: str) -> None:
        try:
            self.last_run = self.runner(question, brain, self._ping, surface=SURFACE)
            self._say(self.last_run.answer.replace("**", ""))   # plain-text messages show ** literally
        except Exception as exc:                    # tell me, don't die
            self._log(f"run failed: {type(exc).__name__}: {exc}")
            self._say(f"That run failed: {type(exc).__name__}: {exc}")
        finally:
            self._current = None
            self._busy.release()

    def _ping(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_ping >= self.ping_every:
            self._last_ping = now
            self._say(f"... {message}")

    def _report(self) -> None:
        if self.last_run is None:
            self._say("Nothing to report yet: ask a question first.")
            return
        path = scout.write_report(self.last_run, self.reports_dir)
        self.api.send_document(self.allowed, path)

    def wait(self, timeout: float = 10.0) -> None:
        """Finish queued runs (tests)."""
        for f in list(self._futures):
            f.result(timeout=timeout)

    # -- the loop ----------------------------------------------------------------------

    def poll_forever(self) -> None:
        offset = None
        self._log("bot started")
        while True:
            try:
                for u in self.api.get_updates(offset):
                    offset = u["update_id"] + 1
                    self.handle(u)
            except Exception as exc:
                self._log(f"poll error: {type(exc).__name__}: {exc}")
                time.sleep(5)


def whoami(api: TelegramAPI, seconds: int = 120) -> None:
    """Print (here, not in Telegram) who messages the bot, so you can find your ID."""
    print(f"Send any message to your bot within {seconds} s...")
    offset, end = None, time.time() + seconds
    while time.time() < end:
        for u in api.get_updates(offset, timeout=10):
            offset = u["update_id"] + 1
            f = (u.get("message") or {}).get("from") or {}
            print(f"user ID {f.get('id')}  (@{f.get('username')}, {f.get('first_name')})")
            api.get_updates(offset, timeout=0)    # mark it read, so the bot doesn't research "hi"
            return


STARTUP_CMD = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "the-scout-telegram.cmd"


def install_startup() -> Path:
    """A .cmd in your Startup folder that runs the bot windowless at login."""
    here = Path(__file__).resolve()
    pyw = Path(os.sys.executable).with_name("pythonw.exe")
    STARTUP_CMD.write_text(f'@echo off\r\ncd /d "{here.parent}"\r\nstart "" "{pyw}" "{here}"\r\n', encoding="utf-8")
    return STARTUP_CMD


PROFILE = "chrome-profile-telegram"   # own profile: the MCP server runs alongside


def main() -> None:
    import sys
    import fetcher
    fetcher.use_profile(PROFILE)
    env = {**scout.load_dotenv(), **os.environ}
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "install-startup":
        print(f"installed: {install_startup()}"); return
    if arg == "remove-startup":
        STARTUP_CMD.unlink(missing_ok=True); print("removed"); return
    token = env.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN missing from .env (create the bot with @BotFather)")
    api = TelegramAPI(token)
    if arg == "whoami":
        whoami(api); return
    Bot(api, env.get("TELEGRAM_ALLOWED_USER_ID")).poll_forever()


if __name__ == "__main__":
    main()
