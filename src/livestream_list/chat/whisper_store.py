"""Local persistence for whisper/DM messages."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..core.models import StreamPlatform
from ..core.settings import get_data_dir
from .models import ChatMessage, ChatUser

logger = logging.getLogger(__name__)

MAX_MESSAGES_PER_CONVERSATION = 200


def _whisper_dir() -> Path:
    """Get the whisper storage directory."""
    path = get_data_dir() / "whispers"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _conversation_path(partner_name: str) -> Path:
    """Get the file path for a conversation with a given partner."""
    safe_name = partner_name.lower().replace("/", "_").replace("\\", "_")
    return _whisper_dir() / f"{safe_name}.json"


def _msg_to_dict(msg: ChatMessage) -> dict:
    """Serialize a ChatMessage to a dict for JSON storage."""
    return {
        "id": msg.id,
        "text": msg.text,
        "timestamp": msg.timestamp.isoformat(),
        "user_id": msg.user.id,
        "user_name": msg.user.name,
        "user_display_name": msg.user.display_name,
        "user_color": msg.user.color,
        "whisper_target": msg.whisper_target,
    }


def _dict_to_msg(d: dict) -> ChatMessage:
    """Deserialize a dict back to a ChatMessage."""
    user = ChatUser(
        id=d.get("user_id", ""),
        name=d.get("user_name", ""),
        display_name=d.get("user_display_name", ""),
        platform=StreamPlatform.TWITCH,
        color=d.get("user_color"),
    )
    try:
        ts = datetime.fromisoformat(d["timestamp"])
    except (KeyError, ValueError):
        ts = datetime.now(timezone.utc)
    return ChatMessage(
        id=d.get("id", ""),
        user=user,
        text=d.get("text", ""),
        timestamp=ts,
        platform=StreamPlatform.TWITCH,
        is_whisper=True,
        whisper_target=d.get("whisper_target"),
    )


def save_whisper(partner_name: str, message: ChatMessage) -> None:
    """Append a whisper message to the conversation file."""
    path = _conversation_path(partner_name)
    messages = _load_raw(path)
    messages.append(_msg_to_dict(message))
    # Keep only the latest N messages
    messages = messages[-MAX_MESSAGES_PER_CONVERSATION:]
    try:
        path.write_text(json.dumps(messages, indent=1), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to save whisper for {partner_name}: {e}")


def load_whispers(partner_name: str) -> list[ChatMessage]:
    """Load all stored whisper messages for a conversation partner."""
    path = _conversation_path(partner_name)
    raw = _load_raw(path)
    messages = []
    for d in raw:
        try:
            messages.append(_dict_to_msg(d))
        except Exception:
            continue
    return messages


def _load_raw(path: Path) -> list[dict]:
    """Load raw JSON array from file."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception as e:
        logger.warning(f"Failed to read whisper file {path}: {e}")
    return []
