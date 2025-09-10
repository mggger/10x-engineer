from dataclasses import dataclass
from datetime import datetime


@dataclass
class SessionInfo:
    """Session information dataclass as specified in tmux-interface-api.md"""
    
    name: str
    created: datetime
    is_attached: bool
    window_count: int
    current_window: str
    session_id: str