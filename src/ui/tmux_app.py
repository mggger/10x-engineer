"""TMuxApp main application class as specified in terminal-ui-api.md"""

import asyncio
import os
from typing import Optional, Dict
from textual.app import App
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, Static
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Input

from lib.tmux_interface import TMuxInterface
from ui.session_sidebar import SessionSidebar
from ui.terminal_widget import TerminalWidget


class DeleteConfirmScreen(ModalScreen[bool]):
    """Modal screen to confirm session deletion"""

    def __init__(self, session_name: str):
        super().__init__()
        self.session_name = session_name

    def compose(self):
        with Vertical(id="dialog"):
            yield Label(f"Delete session '{self.session_name}'?", id="question")
            yield Label("This action cannot be undone.", id="warning")
            with Horizontal(id="buttons"):
                yield Button("Cancel", variant="default", id="cancel")
                yield Button("Delete", variant="error", id="delete")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "delete":
            self.dismiss(True)
        else:
            self.dismiss(False)


class RenameSessionScreen(ModalScreen[Optional[str]]):
    """Modal screen to rename a session. Calls back into the app on save."""

    def __init__(self, current_name: str):
        super().__init__()
        self.current_name = current_name
        self._input: Optional[Input] = None

    def compose(self):
        with Vertical(id="dialog"):
            yield Label(f"Rename session '{self.current_name}'", id="question")
            self._input = Input(value=self.current_name, placeholder="New session name", id="rename-input")
            yield self._input
            with Horizontal(id="buttons"):
                yield Button("Cancel", variant="default", id="cancel")
                yield Button("Save", variant="primary", id="save")

    async def on_mount(self) -> None:
        if self._input:
            self._input.focus()
            self._input.cursor_position = len(self._input.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save" and self._input:
            new_name = self._input.value.strip()
            if new_name:
                # Call back into app asynchronously
                self.app.run_worker(self.app.handle_rename_confirm(self.current_name, new_name), exclusive=True)
                self.dismiss(None)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "rename-input":
            new_name = event.value.strip()
            if new_name:
                self.app.run_worker(self.app.handle_rename_confirm(self.current_name, new_name), exclusive=True)
                self.dismiss(None)
            else:
                self.dismiss(None)


class CopyTextScreen(ModalScreen[None]):
    """Full-screen modal to display terminal text for clean selection/copy."""

    def __init__(self, content: str):
        super().__init__()
        self.content = content
        self._copied_hint: Optional[Label] = None

    def compose(self):
        with Vertical(id="copy-dialog"):
            yield Label("复制模式 (按 Esc 退出)", id="copy-title")
            yield Label(
                "提示：在 iTerm2 中按住 Option 键拖动可进行原生选择复制。按 Y 复制全部内容。",
                id="copy-help",
            )
            self._copied_hint = Label("", id="copy-hint")
            yield self._copied_hint
            with VerticalScroll():
                yield Static(self.content, id="copy-content")

    def on_key(self, event):
        # Close with Escape
        key = getattr(event, "key", "").lower()
        if key == "escape":
            event.prevent_default()
            event.stop()
            self.dismiss(None)
            return
        # Press 'y' to copy all content to clipboard
        if key == "y":
            event.prevent_default()
            event.stop()
            # Best-effort copy to system clipboard
            self.app.run_worker(self._write_clipboard_text(self.content), exclusive=True)
            if self._copied_hint is not None:
                self._copied_hint.update("已复制全部内容到系统剪贴板")

    async def _write_clipboard_text(self, text: str) -> None:
        """Local clipboard write helper for the copy modal."""
        candidates = [
            ["pbcopy"],  # macOS
            ["xclip", "-selection", "clipboard"],  # X11
            ["wl-copy"],  # Wayland
            ["powershell", "-NoProfile", "-Command", "Set-Clipboard"],  # Windows PS
            ["clip"],  # Windows legacy
        ]
        for cmd in candidates:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                assert proc.stdin is not None
                proc.stdin.write(text.encode("utf-8", errors="replace"))
                await proc.stdin.drain()
                proc.stdin.close()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    proc.kill()
                if proc.returncode == 0:
                    return
            except Exception:
                continue


class TMuxApp(App[None]):
    """Main Textual application with tmux session management"""

    def _detect_terminal_theme(self) -> Dict[str, str]:
        """Detect terminal color scheme from environment"""
        # Check for common terminal theme environment variables
        term_program = os.getenv('TERM_PROGRAM', '').lower()
        colorterm = os.getenv('COLORTERM', '').lower()
        iterm_profile = os.getenv('ITERM_PROFILE', '').lower()

        # Default dark theme colors (VS Code inspired)
        theme = {
            'bg': '#1e1e1e',
            'fg': '#d4d4d4',
            'cursor': '#ffffff'
        }

        # iTerm2 theme detection
        if term_program == 'iterm.app':
            if 'light' in iterm_profile:
                theme = {
                    'bg': '#ffffff',
                    'fg': '#000000',
                    'cursor': '#000000'
                }
            elif 'dark' in iterm_profile or 'solarized' in iterm_profile:
                theme = {
                    'bg': '#002b36',
                    'fg': '#839496',
                    'cursor': '#93a1a1'
                }

        # macOS Terminal detection
        elif term_program == 'apple_terminal':
            # Try to get terminal background from system
            theme = {
                'bg': '#000000',
                'fg': '#ffffff',
                'cursor': '#ffffff'
            }

        # Check for light/dark mode preference
        if os.getenv('TERM_THEME') == 'light':
            theme = {
                'bg': '#ffffff',
                'fg': '#000000',
                'cursor': '#000000'
            }
        elif os.getenv('TERM_THEME') == 'dark':
            theme = {
                'bg': '#000000',
                'fg': '#ffffff',
                'cursor': '#ffffff'
            }

        return theme

    BINDINGS = [
        # Forward Ctrl+C to terminal when it has focus (do not quit app)
        Binding("ctrl+c", "terminal_interrupt", "SIGINT", show=False),
        # Sidebar actions use simple letter keys; they only fire when sidebar is focused.
        Binding("b", "new_session", "New Session"),
        Binding("d", "delete_session", "Delete Session"),
        Binding("e", "rename_session", "Rename Session"),
        Binding("p", "toggle_pin", "Pin/Unpin"),
        Binding("q", "quit", "Quit"),
        Binding("up", "sidebar_up", "Select Previous"),
        Binding("down", "sidebar_down", "Select Next"),
        Binding("enter", "attach_session", "Attach to Session"),
        Binding("tab", "focus_next", "Focus Next"),
        Binding("shift+tab", "focus_previous", "Focus Previous"),
        Binding("escape", "focus_sidebar", "Focus Sidebar"),
    ]

    @property
    def CSS(self) -> str:
        """Generate CSS with dynamic terminal colors"""
        return f"""
    SessionSidebar {{
        width: {self.sidebar_width}%;
        border: round $primary;
        margin: 0 1 0 0;
    }}
    
    SessionSidebar.hidden {{
        width: 0%;
        display: none;
    }}
    
    SessionEntry {{
        height: 3;
        margin: 1 0;
    }}
    
    SessionEntry.--active {{
        background: $success 20%;
        color: $success;
    }}
    
    SessionEntry.--selected {{
        background: $primary 30%;
    }}
    
    .main-content {{
        width: {self.terminal_width}%;
        border: round $secondary;
        margin: 0;
    }}
    
    .main-content.fullscreen {{
        width: 100%;
        margin: 0;
        border: none;
    }}
    /* Ensure terminal content uses every column when fullscreen */
    .main-content.fullscreen .terminal-output {{
        width: 100%;
        padding: 0;
    }}
    
    .session-header {{
        height: 1;
        width: 100%;
        background: $primary 20%;
        color: $primary;
        padding: 0 1;
        text-style: bold;
        content-align: left middle;
    }}
    
    /* Scrollable area that contains the terminal text */
    .terminal-scroll {{
        height: 1fr;
        width: 100%;
        overflow-y: auto;
        scrollbar-gutter: stable;
    }}
    .terminal-output {{
        width: 100%;
        background: {self.terminal_colors['bg']};
        color: {self.terminal_colors['fg']};
        padding: 0 1;
    }}
    
    .terminal-input {{
        height: 3;
        margin: 1 0;
        border: round $primary;
        background: {self.terminal_colors['bg']};
        color: {self.terminal_colors['fg']};
    }}

    
    
    SessionSidebar:focus-within {{
        border: round $accent;
    }}
    
    TerminalWidget:focus-within {{
        border: round $accent;
    }}
    
    DeleteConfirmScreen {{
        align: center middle;
    }}
    
    #dialog {{
        width: 50;
        height: 9;
        border: thick $background 80%;
        background: $surface;
        padding: 1;
    }}
    
    #question {{
        width: 100%;
        content-align: center middle;
        text-style: bold;
        margin: 1 0;
    }}
    
    #warning {{
        width: 100%;
        content-align: center middle;
        color: $warning;
        margin: 0 0 2 0;
    }}
    
    #buttons {{
        width: 100%;
        height: 3;
        align: center middle;
    }}

    /* Copy modal */
    CopyTextScreen {{
        align: center middle;
    }}
    #copy-dialog {{
        width: 90%;
        height: 90%;
        border: thick $background 80%;
        background: {self.terminal_colors['bg']};
        padding: 1;
    }}
    #copy-title {{
        width: 100%;
        content-align: center middle;
        text-style: bold;
        margin: 0 0 1 0;
    }}
    #copy-help {{
        width: 100%;
        content-align: left middle;
        color: {self.terminal_colors['fg']} 70%;
        margin: 0 0 1 0;
    }}
    #copy-content {{
        width: 100%;
        height: 1fr;
        background: {self.terminal_colors['bg']};
        color: {self.terminal_colors['fg']};
        padding: 1;
        overflow-y: auto;
        border: round $secondary;
    }}
    #copy-hint {{
        width: 100%;
        content-align: left middle;
        color: $success;
        height: auto;
        margin: 0 0 1 0;
    }}
    """

    def __init__(self, sidebar_width: int = 20):
        super().__init__()
        self.tmux_interface = TMuxInterface()
        self.sidebar: Optional[SessionSidebar] = None
        self.terminal_widget: Optional[TerminalWidget] = None
        self.refresh_task: Optional[asyncio.Task] = None
        self.terminal_colors = self._detect_terminal_theme()
        self.sidebar_visible = True  # Track sidebar visibility
        # Configurable sidebar width (percentage)
        self.sidebar_width = max(10, min(50, sidebar_width))  # Constrain between 10% and 50%
        self.terminal_width = 100 - self.sidebar_width

    def compose(self):
        """Compose the application layout"""
        yield Header()
        with Horizontal():
            yield SessionSidebar(id="session-sidebar")
            yield TerminalWidget(id="terminal-widget", classes="main-content")
        yield Footer()

    async def on_mount(self) -> None:
        """Initialize app components and start session monitoring"""
        self.sidebar = self.query_one("#session-sidebar", SessionSidebar)
        self.terminal_widget = self.query_one("#terminal-widget", TerminalWidget)

        # Initial session load
        await self._refresh_sessions()

        # Start periodic refresh
        self.refresh_task = asyncio.create_task(self._refresh_loop())

    async def on_unmount(self) -> None:
        """Cleanup resources when app exits"""
        if self.refresh_task:
            self.refresh_task.cancel()
            try:
                await self.refresh_task
            except asyncio.CancelledError:
                pass

    async def on_resize(self, event) -> None:
        """Handle terminal resize events"""
        # If terminal widget has an active session, resize it to match new dimensions
        if self.terminal_widget and self.terminal_widget.current_session:
            terminal_size = self.size
            if self.sidebar_visible:
                # With sidebar: use almost all of the terminal area (minus small margin for borders)
                width = max(60, int(terminal_size.width * (self.terminal_width / 100)) - 1)
            else:
                # Fullscreen: use maximum available width
                width = max(80, terminal_size.width - 1)

            # Use maximum available height (only account for app header/footer)
            height = max(24, terminal_size.height - 2)

            await self.tmux_interface.resize_session(
                self.terminal_widget.current_session.name, width, height
            )

    async def action_new_session(self) -> None:
        """Action handler for Command+B - creates new tmux session"""
        # Only when sidebar is focused
        if not self.sidebar or self.focused is not self.sidebar:
            return
        # For now, create a session with timestamp name
        # In a full implementation, this would prompt for a name
        import time
        session_name = f"session_{int(time.time())}"

        success = await self.tmux_interface.create_session(session_name, detached=True)
        if success:
            await self._refresh_sessions()

    async def action_sidebar_up(self) -> None:
        """Move selection up in sidebar (only when sidebar is focused)"""
        if self.sidebar and self.focused is self.sidebar:
            self.sidebar.select_previous()

    async def action_sidebar_down(self) -> None:
        """Move selection down in sidebar (only when sidebar is focused)"""
        if self.sidebar and self.focused is self.sidebar:
            self.sidebar.select_next()

    async def action_attach_session(self) -> None:
        """Attach to currently selected session in the terminal widget"""
        if self.sidebar and self.terminal_widget and self.focused is self.sidebar:
            selected_session = self.sidebar.get_selected_session()
            if selected_session:
                await self.terminal_widget.set_session(selected_session)
                # Go fullscreen: hide sidebar and focus terminal
                await self.hide_sidebar()
                self.terminal_widget.focus()

    async def action_rename_session(self) -> None:
        """Prompt to rename the currently selected session (when sidebar focused)"""
        if not self.sidebar:
            return
        if self.focused is not self.sidebar:
            return
        selected_session = self.sidebar.get_selected_session()
        if not selected_session:
            return

        # Show modal without waiting; screen will call back into app on save
        self.push_screen(RenameSessionScreen(selected_session.name))

    async def handle_rename_confirm(self, old_name: str, new_name: str) -> None:
        """Handle rename confirmation from the modal screen."""
        if not new_name or new_name == old_name:
            return
        success = await self.tmux_interface.rename_session(old_name, new_name)
        if success:
            await self._refresh_sessions()
            if self.sidebar:
                await self.sidebar.select_session(new_name)
                self.sidebar.focus()

    async def action_focus_sidebar(self) -> None:
        """Focus the sidebar for session navigation"""
        # When in fullscreen (sidebar hidden), show it back; otherwise just focus
        if not self.sidebar_visible:
            await self.show_sidebar()
        elif self.sidebar:
            self.sidebar.focus()

    async def action_focus_next(self) -> None:
        """Focus next focusable widget"""
        self.focus_next()

    async def action_focus_previous(self) -> None:
        """Focus previous focusable widget"""
        self.focus_previous()

    async def action_terminal_interrupt(self) -> None:
        """Send Ctrl+C to the terminal when it has focus."""
        if self.terminal_widget and self.focused is self.terminal_widget:
            await self.terminal_widget.send_ctrl_c()
        # If not focused on terminal, ignore to avoid quitting the app

    async def action_force_interrupt(self) -> None:
        """Force-send SIGINT to the pane's foreground process group (Ctrl+Alt+C)."""
        if self.terminal_widget and self.focused is self.terminal_widget:
            await self.terminal_widget.force_interrupt()

    def action_delete_session(self) -> None:
        """Delete the currently selected session with confirmation"""
        # Only when sidebar is focused
        if not self.sidebar or self.focused is not self.sidebar:
            return

        selected_session = self.sidebar.get_selected_session()
        if not selected_session:
            return

        asyncio.create_task(self._delete_session(selected_session.name))

    async def action_toggle_pin(self) -> None:
        """Pin/unpin the currently selected session; only when sidebar focused"""
        if not self.sidebar or self.focused is not self.sidebar:
            return
        await self.sidebar.toggle_pin_selected()

    async def _delete_session(self, session_name: str) -> None:
        """Helper to delete a session immediately"""
        try:
            # å¦‚æžœ terminal æ­£åœ¨æ˜¾ç¤ºè¿™ä¸ª sessionï¼Œå…ˆæ¸…ç©º
            if (self.terminal_widget and
                    self.terminal_widget.current_session and
                    self.terminal_widget.current_session.name == session_name):
                await self.terminal_widget.clear_session()

            # åˆ é™¤ tmux session
            success = await self.tmux_interface.kill_session(session_name)

            if success:
                await self._refresh_sessions()
                if self.sidebar:
                    self.sidebar.focus()
            else:
                # å¯ä»¥åœ¨è¿™é‡ŒåŠ ä¸€ä¸ªé”™è¯¯æç¤º
                pass

        except Exception:
            # å¼‚å¸¸å¤„ç†ï¼ˆå¯åŠ é”™è¯¯æç¤ºï¼‰
            pass

    async def _refresh_sessions(self):
        """Refresh session list from tmux"""
        try:
            sessions = await self.tmux_interface.list_sessions()
            if self.sidebar:
                await self.sidebar.update_sessions(sessions)
        except Exception:
            # Handle tmux errors gracefully
            pass

    async def _refresh_loop(self):
        """Periodic session refresh loop"""
        while True:
            try:
                await asyncio.sleep(60.0)  # Refresh every minute
                await self._refresh_sessions()
            except asyncio.CancelledError:
                break
            except Exception:
                # Continue on errors
                continue

    async def hide_sidebar(self) -> None:
        """Hide sidebar and make terminal fullscreen"""
        self.sidebar_visible = False
        if self.sidebar:
            self.sidebar.add_class("hidden")
        if self.terminal_widget:
            self.terminal_widget.add_class("fullscreen")
            # Resize tmux session to use full terminal width
            if self.terminal_widget.current_session:
                # Get terminal size and use full available width
                terminal_size = self.size
                # Use full width to match fullscreen content area
                width = max(1, terminal_size.width)
                height = max(24, terminal_size.height - 4)  # Account for header/footer only
                await self.tmux_interface.resize_session(
                    self.terminal_widget.current_session.name, width, height
                )

    async def show_sidebar(self) -> None:
        """Show sidebar and restore normal layout"""
        self.sidebar_visible = True
        if self.sidebar:
            self.sidebar.remove_class("hidden")
        if self.terminal_widget:
            self.terminal_widget.remove_class("fullscreen")
            # Resize tmux session back to sidebar layout
            if self.terminal_widget.current_session:
                terminal_size = self.size
                # Use actual terminal_width percentage of available width with minimal padding
                width = max(60, int(terminal_size.width * (self.terminal_width / 100)) - 1)
                height = max(24, terminal_size.height - 2)  # Account for header/footer only
                await self.tmux_interface.resize_session(
                    self.terminal_widget.current_session.name, width, height
                )
        # Focus sidebar when showing
        if self.sidebar:
            self.sidebar.focus()

    async def _update_terminal_preview(self) -> None:
        """Update terminal widget to preview the currently selected session"""
        if self.sidebar and self.terminal_widget:
            selected_session = self.sidebar.get_selected_session()
            if selected_session:
                await self.terminal_widget.set_session(selected_session)
            else:
                await self.terminal_widget.clear_session()
