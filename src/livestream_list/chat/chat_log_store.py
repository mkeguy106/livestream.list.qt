"""Persistent JSONL/text chat log storage with disk rotation."""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from ..core.models import StreamPlatform
from ..core.settings import ChatLoggingSettings, get_data_dir
from .models import ChatMessage, ChatUser

logger = logging.getLogger(__name__)


def _chat_log_dir() -> Path:
    """Get the base chat log storage directory."""
    path = get_data_dir() / "chat_logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _channel_log_dir(channel_key: str) -> Path:
    """Get the log directory for a specific channel."""
    safe_name = channel_key.replace(":", "_").replace("/", "_").replace("\\", "_")
    path = _chat_log_dir() / safe_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _msg_to_dict(msg: ChatMessage) -> dict:
    """Serialize a ChatMessage to a compact dict for JSONL storage."""
    d: dict = {
        "id": msg.id,
        "ts": msg.timestamp.isoformat(),
        "uid": msg.user.id,
        "un": msg.user.name,
        "dn": msg.user.display_name,
        "p": msg.platform.value,
        "t": msg.text,
    }
    if msg.user.color:
        d["c"] = msg.user.color
    if msg.is_system:
        d["sys"] = msg.system_text
    if msg.is_action:
        d["act"] = True
    if msg.reply_parent_display_name:
        d["rp"] = msg.reply_parent_display_name
    if msg.reply_parent_text:
        d["rt"] = msg.reply_parent_text
    if msg.is_raid:
        d["raid"] = msg.raid_viewer_count
    return d


def _dict_to_msg(d: dict) -> ChatMessage:
    """Deserialize a dict back to a ChatMessage for history loading."""
    try:
        platform = StreamPlatform(d.get("p", "twitch"))
    except ValueError:
        platform = StreamPlatform.TWITCH

    user = ChatUser(
        id=d.get("uid", ""),
        name=d.get("un", ""),
        display_name=d.get("dn", ""),
        platform=platform,
        color=d.get("c"),
    )
    try:
        ts = datetime.fromisoformat(d["ts"])
    except (KeyError, ValueError):
        ts = datetime.now(timezone.utc)

    raid_count = d.get("raid", 0)

    return ChatMessage(
        id=d.get("id", ""),
        user=user,
        text=d.get("t", ""),
        timestamp=ts,
        platform=platform,
        is_system=bool(d.get("sys")),
        system_text=d.get("sys", ""),
        is_action=bool(d.get("act")),
        reply_parent_display_name=d.get("rp", ""),
        reply_parent_text=d.get("rt", ""),
        is_raid=bool(raid_count),
        raid_viewer_count=raid_count,
    )


def _msg_to_text(msg: ChatMessage) -> str:
    """Format a ChatMessage as a plain-text log line."""
    ts = msg.timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    if msg.is_system:
        return f"[{ts}] * {msg.system_text}"
    prefix = f"[{ts}] {msg.user.display_name}"
    if msg.is_action:
        return f"{prefix} {msg.text}"
    return f"{prefix}: {msg.text}"


class ChatLogWriter:
    """Buffers and writes chat messages to per-channel log files.

    Supports JSONL and plain text formats with configurable disk limits.
    """

    def __init__(self, settings: ChatLoggingSettings):
        self._settings = settings
        self._buffers: dict[str, list[ChatMessage]] = {}
        self._last_flush: float = time.monotonic()

    @property
    def settings(self) -> ChatLoggingSettings:
        return self._settings

    @settings.setter
    def settings(self, value: ChatLoggingSettings) -> None:
        self._settings = value

    def append(self, channel_key: str, messages: list[ChatMessage]) -> None:
        """Buffer messages for later flushing to disk."""
        if not self._settings.enabled:
            return
        buf = self._buffers.setdefault(channel_key, [])
        buf.extend(messages)
        # Auto-flush if buffer is large
        if len(buf) >= 50:
            self._flush_channel(channel_key)

    def flush_all(self) -> None:
        """Flush all buffered messages to disk."""
        if not self._settings.enabled:
            return
        for channel_key in list(self._buffers.keys()):
            self._flush_channel(channel_key)
        self._last_flush = time.monotonic()

    def _flush_channel(self, channel_key: str) -> None:
        """Write buffered messages for a channel to disk."""
        buf = self._buffers.pop(channel_key, [])
        if not buf:
            return

        log_dir = _channel_log_dir(channel_key)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ext = "jsonl" if self._settings.log_format == "jsonl" else "txt"
        log_file = log_dir / f"{today}.{ext}"

        try:
            with open(log_file, "a", encoding="utf-8") as f:
                if self._settings.log_format == "jsonl":
                    for msg in buf:
                        f.write(json.dumps(_msg_to_dict(msg), ensure_ascii=False) + "\n")
                else:
                    for msg in buf:
                        f.write(_msg_to_text(msg) + "\n")
        except Exception as e:
            logger.warning(f"Failed to write chat log for {channel_key}: {e}")

    def should_flush(self) -> bool:
        """Check if enough time has elapsed for a periodic flush."""
        return time.monotonic() - self._last_flush >= 5.0

    def enforce_disk_limit(self) -> None:
        """Delete oldest log files when total disk usage exceeds the limit."""
        max_bytes = self._settings.max_disk_mb * 1024 * 1024
        base_dir = _chat_log_dir()

        # Collect all log files with their sizes and modification times
        files: list[tuple[float, int, Path]] = []
        try:
            for channel_dir in base_dir.iterdir():
                if not channel_dir.is_dir():
                    continue
                for log_file in channel_dir.iterdir():
                    if log_file.is_file():
                        stat = log_file.stat()
                        files.append((stat.st_mtime, stat.st_size, log_file))
        except Exception as e:
            logger.warning(f"Error scanning chat logs: {e}")
            return

        total_size = sum(size for _, size, _ in files)
        if total_size <= max_bytes:
            return

        # Sort by modification time (oldest first) and delete until under limit
        files.sort(key=lambda x: x[0])
        for mtime, size, path in files:
            if total_size <= max_bytes:
                break
            try:
                path.unlink()
                total_size -= size
                # Clean up empty directories
                parent = path.parent
                if parent != base_dir and not any(parent.iterdir()):
                    parent.rmdir()
            except Exception as e:
                logger.warning(f"Failed to delete old log file {path}: {e}")

    def load_recent_history(
        self, channel_key: str, max_lines: int | None = None
    ) -> list[ChatMessage]:
        """Load recent chat history from JSONL log files.

        Args:
            channel_key: The channel to load history for.
            max_lines: Max messages to load (defaults to settings.history_lines).

        Returns:
            List of ChatMessage objects, oldest first.
        """
        if max_lines is None:
            max_lines = self._settings.history_lines

        log_dir = _channel_log_dir(channel_key)
        if not log_dir.exists():
            return []

        # Only support JSONL for history loading (text format is display-only)
        jsonl_files = sorted(log_dir.glob("*.jsonl"), reverse=True)
        if not jsonl_files:
            return []

        messages: list[ChatMessage] = []
        for log_file in jsonl_files:
            if len(messages) >= max_lines:
                break
            try:
                lines = log_file.read_text(encoding="utf-8").strip().split("\n")
                for line in reversed(lines):
                    if len(messages) >= max_lines:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        messages.append(_dict_to_msg(d))
                    except (json.JSONDecodeError, KeyError):
                        continue
            except Exception as e:
                logger.warning(f"Failed to read log file {log_file}: {e}")
                continue

        # Reverse so oldest is first
        messages.reverse()
        return messages

    def get_total_disk_usage(self) -> int:
        """Get total disk usage of all chat logs in bytes."""
        base_dir = _chat_log_dir()
        total = 0
        try:
            for channel_dir in base_dir.iterdir():
                if not channel_dir.is_dir():
                    continue
                for log_file in channel_dir.iterdir():
                    if log_file.is_file():
                        total += log_file.stat().st_size
        except Exception:
            pass
        return total
