"""Stream list model for virtualized QListView."""

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

from ...core.models import Livestream

# Custom data roles
StreamRole = Qt.ItemDataRole.UserRole + 1  # Returns Livestream object
PlayingRole = Qt.ItemDataRole.UserRole + 2  # Returns bool (is stream playing)
SelectionRole = Qt.ItemDataRole.UserRole + 3  # Returns bool (is row selected in selection mode)


class StreamListModel(QAbstractListModel):
    """Model holding Livestream objects for a virtualized stream list.

    Unlike QListWidget which creates a widget per item, this model only stores
    data. The delegate handles all painting, making it orders of magnitude
    faster for large lists (343+ streams).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._streams: list[Livestream] = []
        self._playing_keys: set[str] = set()
        self._selected_keys: set[str] = set()
        self._selection_mode: bool = False

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        """Return the number of streams in the model."""
        if parent.isValid():
            return 0
        return len(self._streams)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        """Return data for the given index and role."""
        if not index.isValid() or index.row() >= len(self._streams):
            return None

        livestream = self._streams[index.row()]

        if role == Qt.ItemDataRole.DisplayRole:
            # Used for accessibility/testing
            return livestream.channel.display_name or livestream.channel.channel_id
        elif role == StreamRole:
            return livestream
        elif role == PlayingRole:
            return livestream.channel.unique_key in self._playing_keys
        elif role == SelectionRole:
            return livestream.channel.unique_key in self._selected_keys

        return None

    def set_streams(self, streams: list[Livestream]) -> None:
        """Replace all streams in the model.

        This is the "slow path" - called when streams change order or
        the set of visible streams changes.
        """
        self.beginResetModel()
        self._streams = list(streams)
        # Clean up selected keys that no longer exist
        current_keys = {s.channel.unique_key for s in streams}
        self._selected_keys &= current_keys
        self.endResetModel()

    def update_streams_in_place(self, streams: list[Livestream]) -> bool:
        """Update stream data without resetting the model.

        Returns True if the update was done in-place (same keys in same order),
        False if a full reset is needed (caller should use set_streams instead).
        """
        if len(streams) != len(self._streams):
            return False

        # Check if keys match in order
        for i, stream in enumerate(streams):
            if stream.channel.unique_key != self._streams[i].channel.unique_key:
                return False

        # Same order - update in place
        self._streams = list(streams)

        # Emit dataChanged for all rows
        if self._streams:
            self.dataChanged.emit(
                self.index(0),
                self.index(len(self._streams) - 1),
                [StreamRole, PlayingRole],
            )
        return True

    def update_playing_keys(self, keys: set[str]) -> None:
        """Update the set of currently playing stream keys.

        This is a lightweight update that only emits dataChanged for affected rows.
        """
        if keys == self._playing_keys:
            return

        # Find which rows changed
        changed_keys = keys.symmetric_difference(self._playing_keys)
        self._playing_keys = set(keys)

        # Emit dataChanged only for affected rows
        for i, stream in enumerate(self._streams):
            if stream.channel.unique_key in changed_keys:
                idx = self.index(i)
                self.dataChanged.emit(idx, idx, [PlayingRole])

    def set_selection_mode(self, enabled: bool) -> None:
        """Enable or disable selection mode."""
        if self._selection_mode == enabled:
            return

        self._selection_mode = enabled
        if not enabled:
            self._selected_keys.clear()

        # Emit layoutChanged to trigger repaint of all visible items
        self.layoutChanged.emit()

    def is_selection_mode(self) -> bool:
        """Return whether selection mode is active."""
        return self._selection_mode

    def toggle_selection(self, index: QModelIndex) -> None:
        """Toggle selection state for a row."""
        if not index.isValid() or not self._selection_mode:
            return

        row = index.row()
        if row >= len(self._streams):
            return

        key = self._streams[row].channel.unique_key
        if key in self._selected_keys:
            self._selected_keys.discard(key)
        else:
            self._selected_keys.add(key)

        self.dataChanged.emit(index, index, [SelectionRole])

    def set_selected(self, index: QModelIndex, selected: bool) -> None:
        """Set selection state for a row."""
        if not index.isValid() or not self._selection_mode:
            return

        row = index.row()
        if row >= len(self._streams):
            return

        key = self._streams[row].channel.unique_key
        was_selected = key in self._selected_keys

        if selected and not was_selected:
            self._selected_keys.add(key)
            self.dataChanged.emit(index, index, [SelectionRole])
        elif not selected and was_selected:
            self._selected_keys.discard(key)
            self.dataChanged.emit(index, index, [SelectionRole])

    def select_all(self) -> None:
        """Select all streams."""
        if not self._selection_mode:
            return

        self._selected_keys = {s.channel.unique_key for s in self._streams}
        if self._streams:
            self.dataChanged.emit(
                self.index(0),
                self.index(len(self._streams) - 1),
                [SelectionRole],
            )

    def deselect_all(self) -> None:
        """Deselect all streams."""
        if not self._selected_keys:
            return

        self._selected_keys.clear()
        if self._streams:
            self.dataChanged.emit(
                self.index(0),
                self.index(len(self._streams) - 1),
                [SelectionRole],
            )

    def get_selected_keys(self) -> list[str]:
        """Return list of selected channel unique_keys."""
        return list(self._selected_keys)

    def get_selection_count(self) -> int:
        """Return the number of selected items."""
        return len(self._selected_keys)

    def get_stream_at(self, index: QModelIndex) -> Livestream | None:
        """Get the livestream at a given index."""
        if not index.isValid() or index.row() >= len(self._streams):
            return None
        return self._streams[index.row()]

    def get_streams(self) -> list[Livestream]:
        """Get all streams in the model."""
        return list(self._streams)
