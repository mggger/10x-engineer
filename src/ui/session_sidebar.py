"""SessionSidebar widget as specified in terminal-ui-api.md"""

from typing import List, Optional
from textual.widget import Widget
from textual.containers import Vertical, VerticalScroll

from models.session_info import SessionInfo
from ui.session_entry import SessionEntry


class SessionSidebar(Widget):
    """Left sidebar widget displaying tmux session list"""
    
    # Make this widget focusable
    can_focus = True
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._sessions: List[SessionInfo] = []
        self._session_entries: List[SessionEntry] = []
        self._selected_index: int = -1
        # Names of pinned sessions, in the order they were pinned
        self._pinned_names: List[str] = []
    
    def compose(self):
        """Compose the sidebar layout"""
        with Vertical():
            yield VerticalScroll(id="session-list")
    
    async def update_sessions(self, sessions: List[SessionInfo]) -> None:
        """Updates sidebar with new session list from tmux"""
        # Remember previously selected session name if any
        prev_selected_name = self.get_selected_session().name if self.get_selected_session() else None

        # Keep a copy and reorder with pinned at top
        self._sessions = self._order_with_pins(sessions.copy())
        
        # Clear existing entries
        session_list = self.query_one("#session-list")
        await session_list.remove_children()
        self._session_entries.clear()
        
        # Create new entries
        for session in self._sessions:
            entry = SessionEntry(session_info=session)
            self._session_entries.append(entry)
            await session_list.mount(entry)
        
        # Restore previous selection if possible, else select first
        if self._sessions:
            if prev_selected_name and await self.select_session(prev_selected_name):
                pass
            else:
                self._selected_index = 0
                self._update_selection()
        else:
            self._selected_index = -1
    
    def get_selected_session(self) -> Optional[SessionInfo]:
        """Returns currently selected session in sidebar"""
        if 0 <= self._selected_index < len(self._sessions):
            return self._sessions[self._selected_index]
        return None
    
    async def select_session(self, session_name: str) -> bool:
        """Programmatically selects session by name"""
        for i, session in enumerate(self._sessions):
            if session.name == session_name:
                self._selected_index = i
                self._update_selection()
                return True
        return False
    
    def select_next(self) -> Optional[SessionInfo]:
        """Moves selection to next session in list"""
        if not self._sessions:
            return None
        
        self._selected_index = (self._selected_index + 1) % len(self._sessions)
        self._update_selection()
        return self.get_selected_session()
    
    def select_previous(self) -> Optional[SessionInfo]:
        """Moves selection to previous session in list"""
        if not self._sessions:
            return None
        
        self._selected_index = (self._selected_index - 1) % len(self._sessions)
        self._update_selection()
        return self.get_selected_session()
    
    def _update_selection(self):
        """Update visual selection state of entries"""
        for i, entry in enumerate(self._session_entries):
            entry.is_selected = (i == self._selected_index)

    # Pinning support
    def _order_with_pins(self, sessions: List[SessionInfo]) -> List[SessionInfo]:
        if not self._pinned_names:
            return sessions
        # Keep only pins that exist
        existing_pin_names = [n for n in self._pinned_names if any(s.name == n for s in sessions)]
        pinned = []
        names_set = set(existing_pin_names)
        for name in existing_pin_names:
            for s in sessions:
                if s.name == name:
                    pinned.append(s)
                    break
        others = [s for s in sessions if s.name not in names_set]
        return pinned + others

    async def toggle_pin_selected(self) -> None:
        """Toggle pin state for the currently selected session and reorder list"""
        selected = self.get_selected_session()
        if not selected:
            return
        await self.toggle_pin_session(selected.name)

    async def toggle_pin_session(self, session_name: str) -> None:
        """Toggle pin state for a session by name and reorder list"""
        if session_name in self._pinned_names:
            self._pinned_names = [n for n in self._pinned_names if n != session_name]
        else:
            # Insert at the beginning to make it highest priority
            self._pinned_names = [session_name] + [n for n in self._pinned_names if n != session_name]

        # Rebuild entries with pinned ordering, preserving selection
        await self.update_sessions(self._sessions)
