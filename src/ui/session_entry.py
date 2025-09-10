"""SessionEntry widget as specified in terminal-ui-api.md"""

from textual.widget import Widget
from textual.widgets import Label
from textual.containers import Horizontal

from models.session_info import SessionInfo


class SessionEntry(Widget):
    """Individual session display item within sidebar"""
    
    def __init__(self, session_info: SessionInfo, **kwargs):
        super().__init__(**kwargs)
        self._session_info = session_info
        self._is_selected = False
    
    @property
    def session_info(self) -> SessionInfo:
        """Associated session information"""
        return self._session_info
    
    @property
    def is_selected(self) -> bool:
        """Whether this entry is currently selected"""
        return self._is_selected
    
    @is_selected.setter
    def is_selected(self, value: bool):
        """Set selection state"""
        self._is_selected = value
        self.add_class("--selected" if value else "--unselected")
        self.remove_class("--unselected" if value else "--selected")
    
    @property
    def is_active(self) -> bool:
        """Whether this session is currently attached in tmux"""
        return self._session_info.is_attached
    
    def compose(self):
        """Compose the session entry layout"""
        # Active indicator (● for attached, ○ for detached)
        indicator = "●" if self.is_active else "○"
        
        # Session name and window count
        name_text = f"{indicator} {self._session_info.name}"
        window_text = f"[{self._session_info.window_count}]"
        
        # Creation time (simplified)
        time_str = self._session_info.created.strftime("%H:%M")
        
        yield Label(f"{name_text} {window_text}", classes="session-name")
        yield Label(f"Created: {time_str}", classes="session-time")
    
    def on_mount(self):
        """Set up initial styling based on session state"""
        if self.is_active:
            self.add_class("--active")
        else:
            self.add_class("--inactive")