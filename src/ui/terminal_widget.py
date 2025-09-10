"""Terminal widget for interacting with tmux sessions"""

import asyncio
import signal
import re
from typing import Optional
from textual.widget import Widget
from textual.widgets import Static
from textual.containers import Vertical, VerticalScroll
from textual import events
from textual.geometry import Offset

from models.session_info import SessionInfo
from lib.tmux_interface import TMuxInterface


class TerminalWidget(Widget):
    """Terminal widget that displays and allows interaction with tmux session content"""
    
    # Make this widget focusable
    can_focus = True
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.tmux_interface = TMuxInterface()
        self.current_session: Optional[SessionInfo] = None
        self._content_area: Optional[Static] = None
        self._session_header: Optional[Static] = None
        self._scroll_container: Optional[VerticalScroll] = None
        self._capture_task: Optional[asyncio.Task] = None
        self._is_active = False
        self._terminal_content = "No session selected"
        self._target_pane: Optional[str] = None
        # Mouse selection state
        self._mouse_start: Optional[Offset] = None
        self._mouse_end: Optional[Offset] = None
        self._selected_text: str = ""
        # How many lines of scrollback to show from tmux history
        self._scrollback_lines: int = 2000
        # Auto-follow to bottom unless user scrolled up
        self._follow_output: bool = True
    
    def compose(self):
        """Compose the terminal widget layout"""
        from textual.containers import VerticalScroll
        with Vertical():
            yield Static("", id="session-header", classes="session-header")
            # Wrap terminal content in a scrollable container so long output can scroll
            with VerticalScroll(id="terminal-scroll", classes="terminal-scroll"):
                yield Static("No session selected", id="terminal-content", classes="terminal-output")
    
    async def on_mount(self) -> None:
        """Initialize terminal widget"""
        self._content_area = self.query_one("#terminal-content", Static)
        self._session_header = self.query_one("#session-header", Static)
        # Prevent inner scroll view from stealing keyboard focus
        try:
            self._scroll_container = self.query_one("#terminal-scroll", VerticalScroll)
            if self._scroll_container is not None:
                self._scroll_container.can_focus = False
        except Exception:
            pass
    
    def _convert_ansi_to_rich(self, text: str) -> str:
        """Convert ANSI color codes to Rich markup for better terminal display"""
        if not text:
            return text
            
        # ANSI color code mapping to Rich color names
        ansi_colors = {
            '30': 'black', '31': 'red', '32': 'green', '33': 'yellow',
            '34': 'blue', '35': 'magenta', '36': 'cyan', '37': 'white',
            '90': 'bright_black', '91': 'bright_red', '92': 'bright_green', '93': 'bright_yellow',
            '94': 'bright_blue', '95': 'bright_magenta', '96': 'bright_cyan', '97': 'bright_white'
        }
        
        result = text
        
        # Convert basic color codes to Rich markup
        # Pattern matches \x1b[31m (red), \x1b[32m (green), etc.
        color_pattern = re.compile(r'\x1b\[(\d+)m')
        
        # For now, just strip ANSI codes but preserve the content
        # TODO: Convert to Rich markup later if needed
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        result = ansi_escape.sub('', result)
        
        return result

    def _update_display(self):
        """Update the terminal display with current content and input line"""
        if self._content_area:
            # Convert ANSI codes to something displayable
            display_content = self._convert_ansi_to_rich(self._terminal_content)
            self._content_area.update(display_content)
            # Auto-follow: keep view pinned to the latest output when following
            try:
                if self._scroll_container is None:
                    self._scroll_container = self.query_one("#terminal-scroll", VerticalScroll)
                if self._scroll_container is not None:
                    # If user scrolled up (not at bottom), disable follow
                    at_bottom = self._is_scroll_at_bottom()
                    if not at_bottom:
                        self._follow_output = False
                    if self._follow_output:
                        try:
                            # Preferred API
                            self._scroll_container.scroll_end(animate=False)  # type: ignore[attr-defined]
                        except Exception:
                            # Fallback for older Textual APIs
                            try:
                                self._scroll_container.scroll_to(0, 10**9)  # type: ignore[attr-defined]
                            except Exception:
                                pass
                    # Re-enable follow automatically when back at bottom
                    self._follow_output = self._is_scroll_at_bottom()
            except Exception:
                pass

    def _is_scroll_at_bottom(self) -> bool:
        """Check if the scroll container is at (or near) bottom."""
        try:
            sc = self._scroll_container
            if sc is None:
                return True
            # Current Y offset
            y = getattr(sc, "scroll_y", None)
            if y is None:
                so = getattr(sc, "scroll_offset", None)
                if so is not None:
                    y = getattr(so, "y", None)
            # Viewport height
            viewport_h = getattr(getattr(sc, "size", None), "height", None)
            # Content height
            content_h = None
            vs = getattr(sc, "virtual_size", None)
            if vs is not None:
                content_h = getattr(vs, "height", None)
            if content_h is None:
                region = getattr(sc, "scrollable_content_region", None)
                if region is not None:
                    content_h = getattr(region, "height", None)
            if y is None or viewport_h is None or content_h is None:
                return True
            max_y = max(content_h - viewport_h, 0)
            return y >= max_y - 1
        except Exception:
            return True

    def on_scroll(self, event) -> None:  # type: ignore[override]
        """Update follow mode when user scrolls (compatible across Textual versions)."""
        try:
            self._follow_output = self._is_scroll_at_bottom()
        except Exception:
            pass
    
    async def on_unmount(self) -> None:
        """Cleanup resources when widget is unmounted"""
        if self._capture_task:
            self._capture_task.cancel()
            try:
                await self._capture_task
            except asyncio.CancelledError:
                pass
    
    async def set_session(self, session_info: SessionInfo) -> None:
        """Set the current session to display in terminal"""
        # Stop any existing capture
        if self._capture_task:
            self._capture_task.cancel()
            try:
                await self._capture_task
            except asyncio.CancelledError:
                pass
        
        self.current_session = session_info
        self._is_active = True
        # Resolve the active pane id for precise targeting
        try:
            self._target_pane = await self.tmux_interface.get_active_pane_id(session_info.name)
        except Exception:
            self._target_pane = None
        
        # Update session header
        if self._session_header:
            self._session_header.update(f"📺 Session: {session_info.name}")
        
        # Update content
        self._terminal_content = f"Connected to session: {session_info.name}"
        self._update_display()
        
        # Focus this widget to capture keystrokes
        self.focus()
        # Follow new session output by default
        self._follow_output = True
        
        # Trigger app resize to adjust session to current container size
        if hasattr(self.app, 'on_resize') and hasattr(self.app, 'size'):
            await self.app.on_resize(None)
            
        # Start capturing session output
        self._capture_task = asyncio.create_task(self._capture_session_output())
    
    async def clear_session(self) -> None:
        """Clear the current session"""
        if self._capture_task:
            self._capture_task.cancel()
            try:
                await self._capture_task
            except asyncio.CancelledError:
                pass
        
        self.current_session = None
        self._is_active = False
        self._target_pane = None
        
        # Clear session header
        if self._session_header:
            self._session_header.update("")
        
        # Update content
        self._terminal_content = "No session selected"
        self._update_display()
        self._follow_output = True

    
    async def send_command(self, command: str) -> bool:
        """Send a full command line followed by Enter to the current session"""
        if not self.current_session:
            return False
        target = self._target_pane or self.current_session.name
        ok = await self.tmux_interface.send_text(target, command)
        if ok:
            return await self.tmux_interface.send_keys(target, "Enter")
        return False
    
    async def _capture_session_output(self) -> None:
        """Capture and display session output"""
        if not self.current_session:
            return
        
        try:
            while True:
                # Get the current pane content
                try:
                    # Refresh target pane periodically in case active pane changed
                    if self.current_session:
                        try:
                            self._target_pane = await self.tmux_interface.get_active_pane_id(self.current_session.name)
                        except Exception:
                            pass
                    target = self._target_pane or (self.current_session.name if self.current_session else "")
                    # Capture tmux pane content
                    try:
                        # Capture last N lines from tmux history for ample scrollback
                        output = await self.tmux_interface._run_tmux_command(
                            "capture-pane", "-t", target, "-S", f"-{self._scrollback_lines}", "-p"
                        )
                    except Exception:
                        output = f"Session '{self.current_session.name}' is active"
                    
                    if output:
                        # Format output for display
                        self._terminal_content = output
                        self._update_display()
                        
                except Exception:
                    # Continue on errors
                    pass
                
                # Update frequently for interactive feel
                await asyncio.sleep(0.1)
                
        except asyncio.CancelledError:
            raise
        except Exception:
            # Handle other errors gracefully
            pass

    async def _resolve_target(self) -> str:
        """Resolve the best tmux target (active pane or session name)."""
        if self.current_session:
            try:
                pane = await self.tmux_interface.get_active_pane_id(self.current_session.name)
                if pane:
                    self._target_pane = pane
                    return pane
            except Exception:
                pass
            return self.current_session.name
        return ""
    
    def on_focus(self) -> None:
        """Focus events - no sub-input to focus anymore"""
        pass

    async def on_key(self, event: events.Key) -> None:
        """Forward key events to tmux for real-time interaction."""
        if not self.current_session:
            return
        
        # Allow input for all sessions for now
        # TODO: Re-enable session ownership checking after fixing session management

        key = event.key or ""
        char = event.character
        ctrl = getattr(event, "control", getattr(event, "ctrl", False)) or False
        meta = getattr(event, "meta", getattr(event, "alt", False)) or False
        shift = getattr(event, "shift", False) or False
        name = getattr(event, "name", "") or key
        lname = name.lower() if isinstance(name, str) else ""

        # Debug logging for Ctrl+C detection
        try:
            self.app.log(f"Key event: key={key}, char={char!r}, ctrl={ctrl}, meta={meta}, name={name}")
        except Exception:
            pass

        # Additional detection for Ctrl+C when Textual passes it as "ctrl+c" string
        if not ctrl and key == "ctrl+c":
            ctrl = True
            char = "c"
            lname = "c"

        # Hard force interrupt: Ctrl+Alt+C (handled here to bypass app-level bindings)
        if (ctrl and meta) and (
            (char and (char == 'c' or ord(char) == 3)) or
            (len(key) == 1 and key.lower() == 'c') or
            ("+" in lname and lname.endswith("c") and "ctrl" in lname and ("alt" in lname or "meta" in lname))
        ):
            event.prevent_default()
            event.stop()
            target = await self._resolve_target()
            ok = await self.tmux_interface.send_signal_to_foreground(target, signal.SIGINT)  # type: ignore
            try:
                self.app.log(f"ForceInterrupt: target={target} tty-foreground ok={ok}")
            except Exception:
                pass
            if not ok:
                ok = await self.tmux_interface.send_signal_to_pane(target, signal.SIGINT)  # type: ignore
                try:
                    self.app.log(f"ForceInterrupt: pane-pgid ok={ok}")
                except Exception:
                    pass
            if not ok:
                await self.tmux_interface.send_keys(target, "C-c")
                try:
                    self.app.log("ForceInterrupt: fallback send-keys C-c")
                except Exception:
                    pass
            return

        # Hard force interrupt: Ctrl+Alt+K to send SIGINT to foreground PGID
        if (ctrl and meta) and (
            (char and (char == 'k')) or
            (len(key) == 1 and key.lower() == 'k') or
            ("+" in lname and lname.endswith("k") and "ctrl" in lname and ("alt" in lname or "meta" in lname))
        ):
            event.prevent_default()
            event.stop()
            target = await self._resolve_target()
            ok = await self.tmux_interface.send_signal_to_foreground(target, signal.SIGINT)  # type: ignore
            if not ok:
                ok = await self.tmux_interface.send_signal_to_pane(target, signal.SIGINT)  # type: ignore
            try:
                self.app.log(f"ForceInterruptK: target={target} ok={ok}")
            except Exception:
                pass
            return

        # Let Escape bubble to the App (used for focusing sidebar)
        if key.lower() == "escape":
            return

        # Copy helpers: Meta+Y copies current pane text; Meta+Shift+Y opens copy modal
        if meta and (key.lower().endswith("y") or (char and char.lower() == "y")):
            event.prevent_default()
            event.stop()
            target = await self._resolve_target()
            try:
                # Try to capture pane content for copy operation
                output = await self.tmux_interface._run_tmux_command(
                    "capture-pane", "-t", target, "-S", f"-{self._scrollback_lines}", "-p"
                )
                if not output:
                    output = f"Session '{target}' - Press keys to interact with terminal"
            except Exception:
                output = getattr(self, "_terminal_content", "") or ""

            if shift:
                # Open modal for clean selection
                try:
                    # type: ignore[attr-defined]
                    from ui.tmux_app import CopyTextScreen  # local import to avoid cycles on import time
                    self.app.push_screen(CopyTextScreen(output))
                except Exception:
                    pass
            else:
                await self._write_clipboard_text(output)
                try:
                    self.app.log("Terminal content copied to clipboard (Meta+Y)")
                except Exception:
                    pass
            return

        # For all other keys, stop App/global bindings (e.g., q, Ctrl+C)
        event.prevent_default()
        event.stop()

        # If Ctrl is held with a letter, or key name encodes ctrl+<letter>, send control chord
        ctrl_letter = None
        # Primary: when control is flagged
        if ctrl:
            if char and len(char) == 1:
                # Detect control characters (ASCII < 32) and map back to letter
                code = ord(char)
                if code < 32:
                    mapped = chr((code + 64) & 0x7F)
                    if mapped.isalpha():
                        ctrl_letter = mapped.lower()
                elif char.isalpha():
                    ctrl_letter = char.lower()
            elif len(key) == 1 and key.isalpha():
                ctrl_letter = key.lower()
            else:
                # Some terminals/textual produce key name like 'ctrl+c' even when ctrl flag is True
                if "+" in lname:
                    parts = lname.split("+")
                    if any(p in ("ctrl", "control") for p in parts[:-1]):
                        last = parts[-1]
                        if len(last) == 1 and last.isalpha():
                            ctrl_letter = last
        else:
            # Fallback: parse textual's key name like 'ctrl+c', 'control+c'
            # Also handle when key is exactly "ctrl+c" format
            if "+" in key:
                parts = key.split("+")
                if any(p in ("ctrl", "control") for p in parts[:-1]):
                    last = parts[-1]
                    if len(last) == 1 and last.isalpha():
                        ctrl_letter = last
            elif "+" in lname:
                parts = lname.split("+")
                if any(p in ("ctrl", "control") for p in parts[:-1]):
                    last = parts[-1]
                    if len(last) == 1 and last.isalpha():
                        ctrl_letter = last
        if ctrl_letter is not None:
            # Special-case Ctrl+V as paste fallback (when terminal doesn't emit Paste event)
            if ctrl_letter == "v":
                pasted = await self._read_clipboard_text()
                if pasted:
                    await self._send_paste_text(pasted)
                return
            # Forward Ctrl+C (interrupt) and other control chords to tmux
            target = await self._resolve_target()
            if ctrl_letter == "c":
                # 1) Try tmux send-keys C-c
                ok = await self.tmux_interface.send_keys(target, "C-c")
                try:
                    self.app.log(f"CtrlC: send-keys ok={ok}")
                except Exception:
                    pass
                # 2) Fallback to TTY foreground process group
                if not ok:
                    ok = await self.tmux_interface.send_signal_to_foreground(target, signal.SIGINT)  # type: ignore
                    try:
                        self.app.log(f"CtrlC: tty-foreground ok={ok}")
                    except Exception:
                        pass
                # 3) Last resort: use pane pid's process group
                if not ok:
                    ok2 = await self.tmux_interface.send_signal_to_pane(target, signal.SIGINT)  # type: ignore
                    try:
                        self.app.log(f"CtrlC: pane-pgid ok={ok2}")
                    except Exception:
                        pass
                return
            await self.tmux_interface.send_keys(target, f"C-{ctrl_letter}")
            return

        special_map = {
            "enter": "Enter",
            "escape": "Escape",
            "tab": "Tab",
            "backspace": "Backspace",
            "delete": "Delete",
            "up": "Up",
            "down": "Down",
            "left": "Left",
            "right": "Right",
            "home": "Home",
            "end": "End",
            "pageup": "PageUp",
            "pagedown": "PageDown",
        }

        # Handle literal text (without Ctrl/Meta)
        # But skip if it looks like a key name (contains +)
        if char and not ctrl and not meta and "+" not in key:
            target = await self._resolve_target()
            await self.tmux_interface.send_text(target, char)
            return

        # Ctrl/Meta chord for letters or known special keys (fallback)
        def chord_name(k: str) -> str:
            parts = []
            if ctrl:
                parts.append("C-")
            if meta:
                parts.append("M-")
            # Normalize letter keys
            if len(k) == 1 and k.isalpha():
                # tmux expects lowercase after C- for control letters, e.g. C-c
                parts.append(k.lower())
            else:
                # Map to tmux key names if available
                mapped = special_map.get(k.lower())
                parts.append(mapped if mapped else k)
            return "".join(parts)

        # If it's a known special
        if key.lower() in special_map and not (ctrl or meta):
            target = await self._resolve_target()
            await self.tmux_interface.send_keys(target, special_map[key.lower()])
            return

        # Handle meta+v (if terminal forwards it; many terminals paste before app sees this)
        if meta and lname.endswith("v"):
            pasted = await self._read_clipboard_text()
            if pasted:
                await self._send_paste_text(pasted)
            return

        # Otherwise, send as a chord (Ctrl/Meta or special with modifiers)
        target = await self._resolve_target()
        # Normalize textual-style names like 'ctrl+c', 'control+c', optionally with meta/alt
        if "+" in key:
            parts = key.split("+")
            mods = [p for p in parts[:-1]]
            last = parts[-1]
            if len(last) == 1 and last.isprintable():
                token = ""
                if any(m in ("ctrl", "control") for m in mods):
                    token += "C-"
                if any(m in ("alt", "meta") for m in mods):
                    token += "M-"
                if last.isalpha():
                    token += last.lower()
                else:
                    mapped = special_map.get(last, last)
                    token += mapped
                await self.tmux_interface.send_keys(target, token)
                return
        elif "+" in lname:
            parts = lname.split("+")
            mods = [p for p in parts[:-1]]
            last = parts[-1]
            if len(last) == 1 and last.isprintable():
                token = ""
                if any(m in ("ctrl", "control") for m in mods):
                    token += "C-"
                if any(m in ("alt", "meta") for m in mods):
                    token += "M-"
                if last.isalpha():
                    token += last.lower()
                else:
                    mapped = special_map.get(last, last)
                    token += mapped
                await self.tmux_interface.send_keys(target, token)
                return
        # Final fallback
        await self.tmux_interface.send_keys(target, chord_name(key))

    async def on_paste(self, event: events.Paste) -> None:
        """Paste text (bracketed paste) directly into the pane with newline handling."""
        if self.current_session and event.text:
            event.prevent_default()
            event.stop()
            await self._send_paste_text(event.text)

    async def _send_paste_text(self, text: str) -> None:
        """Send pasted text, preserving newlines as Enter presses and chunking long lines."""
        if not self.current_session or not text:
            return

        # Normalize newlines and split
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.split("\n")

        for i, line in enumerate(lines):
            # Send in chunks to avoid command-length limits
            if line:
                target = await self._resolve_target()
                await self._send_text_in_chunks(target, line)
            # Recreate newline as Enter between lines (not after last line)
            if i < len(lines) - 1:
                target = await self._resolve_target()
                await self.tmux_interface.send_keys(target, "Enter")

    async def _send_text_in_chunks(self, target: str, text: str, chunk_size: int = 1024) -> None:
        """Helper to send long text safely in chunks via tmux send-keys -l."""
        start = 0
        end = len(text)
        while start < end:
            chunk = text[start:start + chunk_size]
            await self.tmux_interface.send_text(target, chunk)
            start += chunk_size

    async def _read_clipboard_text(self) -> Optional[str]:
        """Best-effort clipboard read across platforms for paste shortcuts.

        Tries, in order:
        - macOS: pbpaste
        - Linux (X11): xclip -selection clipboard -o
        - Linux (Wayland): wl-paste -n
        - Windows: powershell Get-Clipboard
        """
        cmds = [
            ["pbpaste"],
            ["xclip", "-selection", "clipboard", "-o"],
            ["wl-paste", "-n"],
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
        ]
        for cmd in cmds:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=1.0)
                if proc.returncode == 0:
                    text = stdout.decode("utf-8", errors="replace")
                    return text
            except Exception:
                continue
        return None

    async def _write_clipboard_text(self, text: str) -> None:
        """Best-effort write to system clipboard across platforms."""
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

    async def send_ctrl_c(self) -> None:
        """Explicitly forward Ctrl+C to the active tmux pane."""
        if not self.current_session:
            return
        target = await self._resolve_target()
        await self.tmux_interface.send_keys(target, "C-c")

    async def force_interrupt(self) -> None:
        """Force-send SIGINT to the foreground process group of the active pane.

        Tries TTY foreground PGID first, then pane pid PGID, then tmux C-c.
        """
        if not self.current_session:
            return
        target = await self._resolve_target()
        ok = await self.tmux_interface.send_signal_to_foreground(target, signal.SIGINT)  # type: ignore
        if not ok:
            ok = await self.tmux_interface.send_signal_to_pane(target, signal.SIGINT)  # type: ignore
        if not ok:
            await self.tmux_interface.send_keys(target, "C-c")
