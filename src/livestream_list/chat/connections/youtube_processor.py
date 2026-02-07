"""Custom pytchat processor that handles moderation events and mode changes.

Extends pytchat's DefaultProcessor to additionally capture:
- markChatItemAsDeletedAction (single message deletion)
- markChatItemsByAuthorAsDeletedAction (ban/timeout - all messages by author)
- liveChatModeChangeMessageRenderer (subscriber-only, slow mode, etc.)
"""

import logging
import threading

from pytchat.processors.default.processor import DefaultProcessor

from ..models import ChatRoomState, ModerationEvent

logger = logging.getLogger(__name__)


class LivestreamListProcessor(DefaultProcessor):
    """Extended pytchat processor with moderation and mode change support."""

    def __init__(self):
        super().__init__()
        self._moderation_events: list[ModerationEvent] = []
        self._room_state_changes: list[ChatRoomState] = []
        self._system_messages: list[tuple[str, str]] = []  # (id, text) pairs
        self._lock = threading.Lock()

    def process(self, chat_components: list):
        """Process chat data, capturing moderation events and mode changes."""
        # Let DefaultProcessor handle normal messages
        chatdata = super().process(chat_components)

        # Now scan for actions the default processor ignores
        if chat_components:
            for component in chat_components:
                if component is None:
                    continue
                actions = component.get("chatdata")
                if actions is None:
                    continue
                for action in actions:
                    if action is None:
                        continue
                    self._process_extra_action(action)

        return chatdata

    def _process_extra_action(self, action: dict) -> None:
        """Process action types that DefaultProcessor ignores."""
        # Single message deletion
        deleted = action.get("markChatItemAsDeletedAction")
        if deleted:
            self._handle_message_deleted(deleted)

        # All messages by author deleted (ban/timeout)
        author_deleted = action.get("markChatItemsByAuthorAsDeletedAction")
        if author_deleted:
            self._handle_author_banned(author_deleted)

        # Mode changes come inside addChatItemAction items
        add_action = action.get("addChatItemAction")
        if add_action:
            item = add_action.get("item")
            if item and "liveChatModeChangeMessageRenderer" in item:
                self._handle_mode_change(item["liveChatModeChangeMessageRenderer"])

    def _handle_message_deleted(self, deleted: dict) -> None:
        """Handle a single message deletion."""
        target_id = deleted.get("targetItemId", "")
        if not target_id:
            # Try alternative key
            deleted_renderer = deleted.get("deletedStateMessage", {})
            target_id = deleted_renderer.get("targetItemId", "")

        if target_id:
            event = ModerationEvent(type="delete", target_message_id=target_id)
            with self._lock:
                self._moderation_events.append(event)
            logger.debug(f"YouTube message deleted: {target_id}")

    def _handle_author_banned(self, author_deleted: dict) -> None:
        """Handle bulk deletion by author (ban/timeout)."""
        channel_id = author_deleted.get("externalChannelId", "")
        if channel_id:
            event = ModerationEvent(type="ban", target_user_id=channel_id)
            with self._lock:
                self._moderation_events.append(event)
            logger.debug(f"YouTube author banned: {channel_id}")

    def _handle_mode_change(self, renderer: dict) -> None:
        """Handle liveChatModeChangeMessageRenderer."""
        # Extract text from runs
        text_runs = renderer.get("text", {}).get("runs", [])
        text = "".join(run.get("text", "") for run in text_runs)
        icon_type = renderer.get("icon", {}).get("iconType", "")

        if not text:
            return

        logger.info(f"YouTube mode change: {text} (icon={icon_type})")

        # Build a room state update based on the change text/icon
        state = self._parse_mode_change(text, icon_type)
        if state:
            with self._lock:
                self._room_state_changes.append(state)

        # Also store as a system message to display in chat
        msg_id = renderer.get("id", "")
        with self._lock:
            self._system_messages.append((msg_id, text))

    def _parse_mode_change(self, text: str, icon_type: str) -> ChatRoomState | None:
        """Parse mode change text into a ChatRoomState."""
        text_lower = text.lower()

        # Subscriber/members-only mode
        if "members-only" in text_lower or icon_type == "TAB_SUBSCRIPTIONS":
            enabled = "on" in text_lower or "enabled" in text_lower
            return ChatRoomState(subs_only=enabled)

        if "subscribers-only" in text_lower:
            enabled = "on" in text_lower or "enabled" in text_lower
            return ChatRoomState(subs_only=enabled)

        # Slow mode
        if "slow mode" in text_lower or icon_type == "SLOW_MODE":
            if "off" in text_lower or "disabled" in text_lower:
                return ChatRoomState(slow=0)
            # Try to extract seconds from the text
            import re

            match = re.search(r"(\d+)\s*(?:second|sec|s)", text_lower)
            seconds = int(match.group(1)) if match else 30  # default 30s
            return ChatRoomState(slow=seconds)

        return None

    def pop_moderation_events(self) -> list[ModerationEvent]:
        """Pop all pending moderation events (thread-safe)."""
        with self._lock:
            events = self._moderation_events[:]
            self._moderation_events.clear()
        return events

    def pop_room_state_changes(self) -> list[ChatRoomState]:
        """Pop all pending room state changes (thread-safe)."""
        with self._lock:
            changes = self._room_state_changes[:]
            self._room_state_changes.clear()
        return changes

    def pop_system_messages(self) -> list[tuple[str, str]]:
        """Pop all pending system messages (thread-safe)."""
        with self._lock:
            messages = self._system_messages[:]
            self._system_messages.clear()
        return messages
