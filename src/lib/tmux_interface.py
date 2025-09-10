"""TMux Interface implementation as specified in tmux-interface-api.md"""

import asyncio
import os
import signal
import termios
import subprocess
import uuid
from typing import List, Optional
from datetime import datetime, timezone

from models.session_info import SessionInfo
from lib.tmux_errors import (
    TMuxError,
    TMuxNotRunningError,
    SessionNotFoundError,
    SessionCreationError,
    CommandExecutionError
)


class TMuxInterface:
    """Direct tmux command integration and session queries"""
    
    def __init__(self, tmux_command: str = "tmux", command_timeout: float = 5.0):
        self.tmux_command = tmux_command
        self.command_timeout = command_timeout
        # Generate unique ID for this tmux-ui instance
        self.instance_id = str(uuid.uuid4())[:8]
    
    async def _run_tmux_command(self, *args: str) -> str:
        """Execute tmux command and return output"""
        cmd = [self.tmux_command] + list(args)
        
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), 
                timeout=self.command_timeout
            )
            
            if process.returncode != 0:
                error_msg = stderr.decode('utf-8').strip()
                if "no server running" in error_msg.lower():
                    raise TMuxNotRunningError(f"tmux server not running: {error_msg}")
                elif "session not found" in error_msg.lower():
                    raise SessionNotFoundError(f"Session not found: {error_msg}")
                elif "duplicate session" in error_msg.lower() or "already exists" in error_msg.lower():
                    raise SessionCreationError(f"Session creation failed: {error_msg}")
                else:
                    raise CommandExecutionError(f"tmux command failed: {error_msg}")
            
            return stdout.decode('utf-8').strip()
            
        except asyncio.TimeoutError:
            raise CommandExecutionError(f"tmux command timed out after {self.command_timeout}s")
        except FileNotFoundError:
            raise TMuxNotRunningError("tmux command not found")
    
    async def list_sessions(self) -> List[SessionInfo]:
        """Retrieves all tmux sessions"""
        try:
            output = await self._run_tmux_command(
                "list-sessions", "-F", 
                "#{session_name}:#{session_created}:#{session_attached}:#{session_windows}:#{window_active}:#{session_id}"
            )
            
            sessions = []
            if output:  # Only process if there's output
                for line in output.split('\n'):
                    if line.strip():
                        parts = line.split(':')
                        if len(parts) >= 6:
                            name = parts[0]
                            created_timestamp = int(parts[1])
                            created = datetime.fromtimestamp(created_timestamp, tz=timezone.utc)
                            is_attached = parts[2] == '1'
                            window_count = int(parts[3])
                            current_window = parts[4]
                            session_id = parts[5]
                            
                            sessions.append(SessionInfo(
                                name=name,
                                created=created,
                                is_attached=is_attached,
                                window_count=window_count,
                                current_window=current_window,
                                session_id=session_id
                            ))
            
            return sessions
            
        except TMuxNotRunningError:
            # If tmux is not running, return empty list (valid state)
            return []
    
    async def get_session_info(self, session_name: str) -> Optional[SessionInfo]:
        """Gets detailed information for specific session"""
        try:
            output = await self._run_tmux_command(
                "display-message", "-t", session_name, "-p",
                "#{session_name}:#{session_created}:#{session_attached}:#{session_windows}:#{window_active}:#{session_id}"
            )
            
            if output:
                parts = output.split(':')
                if len(parts) >= 6:
                    name = parts[0]
                    created_timestamp = int(parts[1])
                    created = datetime.fromtimestamp(created_timestamp, tz=timezone.utc)
                    is_attached = parts[2] == '1'
                    window_count = int(parts[3])
                    current_window = parts[4]
                    session_id = parts[5]
                    
                    return SessionInfo(
                        name=name,
                        created=created,
                        is_attached=is_attached,
                        window_count=window_count,
                        current_window=current_window,
                        session_id=session_id
                    )
            
            return None
            
        except SessionNotFoundError:
            return None
    
    async def session_exists(self, session_name: str) -> bool:
        """Quick check if session exists without full metadata query"""
        try:
            await self._run_tmux_command("has-session", "-t", session_name)
            return True
        except (SessionNotFoundError, TMuxNotRunningError, CommandExecutionError):
            return False
    
    async def create_session(self, name: str, detached: bool = True) -> bool:
        """Creates new tmux session with tmux-ui tag"""
        try:
            args = ["new-session", "-s", name]
            if detached:
                args.append("-d")
            
            await self._run_tmux_command(*args)
            
            # Tag session as managed by this tmux-ui instance
            await self._run_tmux_command("set-option", "-t", name, "@tmux-ui-managed", "true")
            await self._run_tmux_command("set-option", "-t", name, "@tmux-ui-instance", self.instance_id)
            return True
            
        except (SessionCreationError, CommandExecutionError):
            return False
    
    async def _is_tmux_ui_managed(self, session_name: str) -> bool:
        """Check if session is managed by this tmux-ui instance"""
        try:
            managed_output = await self._run_tmux_command(
                "show-options", "-t", session_name, "-v", "@tmux-ui-managed"
            )
            if managed_output.strip() != "true":
                return False
                
            instance_output = await self._run_tmux_command(
                "show-options", "-t", session_name, "-v", "@tmux-ui-instance"
            )
            return instance_output.strip() == self.instance_id
        except:
            return False
    
    async def switch_to_session(self, session_name: str) -> bool:
        """Switches tmux client to specified session"""
        try:
            await self._run_tmux_command("switch-client", "-t", session_name)
            return True
        except (SessionNotFoundError, CommandExecutionError):
            return False
    
    async def kill_session(self, session_name: str) -> bool:
        """Terminates specified tmux session"""
        try:
            await self._run_tmux_command("kill-session", "-t", session_name)
            return True
        except (SessionNotFoundError, CommandExecutionError):
            return False

    async def rename_session(self, old_name: str, new_name: str) -> bool:
        """Renames a tmux session from old_name to new_name"""
        try:
            await self._run_tmux_command("rename-session", "-t", old_name, new_name)
            return True
        except (SessionNotFoundError, SessionCreationError, CommandExecutionError):
            return False
    
    async def is_tmux_running(self) -> bool:
        """Checks if tmux server is active"""
        try:
            await self._run_tmux_command("list-sessions")
            return True
        except:
            return False
    
    async def get_tmux_version(self) -> str:
        """Gets tmux version string"""
        try:
            output = await self._run_tmux_command("-V")
            # Output format: "tmux 3.3a"
            return output.split()[-1] if output else "unknown"
        except Exception as e:
            raise TMuxError(f"Unable to get tmux version: {e}")

    async def resize_session(self, session_name: str, width: int, height: int) -> bool:
        """Resize tmux session by resizing all windows/panes to specified dimensions"""
        try:
            # Method 1: Resize the current window in the session
            await self._run_tmux_command("resize-window", "-t", f"{session_name}:.", "-x", str(width), "-y", str(height))
            return True
        except Exception as e1:
            # Method 2: Fallback to resizing the active pane
            try:
                pane_id = await self.get_active_pane_id(session_name)
                if pane_id:
                    await self._run_tmux_command("resize-pane", "-t", pane_id, "-x", str(width), "-y", str(height))
                    return True
            except Exception as e2:
                # Method 3: Force resize by switching to session and using refresh-client
                try:
                    # This forces the session to adapt to the specified size
                    await self._run_tmux_command("refresh-client", "-t", session_name, "-S", f"{width}x{height}")
                    return True
                except Exception:
                    pass
            return False

    async def send_keys(self, target: str, *keys: str) -> bool:
        """Send key presses to a tmux target (pane/window/session).

        - `target` can be a pane id (e.g., "%1"), window, or session name.
        - Keys may include names like 'Enter', 'Escape', 'Backspace', 'Up', 'Down',
          or tmux-style chord names like 'C-c', 'M-x', 'C-Left'.
        """
        try:
            await self._run_tmux_command("send-keys", "-t", target, *keys)
            return True
        except Exception:
            return False

    async def send_text(self, target: str, text: str) -> bool:
        """Send literal text to a tmux target (no implicit Enter)."""
        if not text:
            return True
        try:
            await self._run_tmux_command("send-keys", "-t", target, "-l", text)
            return True
        except Exception:
            return False

    async def get_active_pane_id(self, session_name: str) -> Optional[str]:
        """Return the active pane id (e.g., "%3") for a given session.

        Uses list-panes to find the active pane reliably even if no client is attached.
        """
        try:
            out = await self._run_tmux_command(
                "list-panes", "-t", session_name, "-F", "#{pane_active}:#{pane_id}"
            )
            if not out:
                return None
            for line in out.split("\n"):
                parts = line.strip().split(":", 1)
                if len(parts) == 2 and parts[0] == "1":
                    return parts[1]
            # Fallback to first pane id if none marked active
            first = out.split("\n")[0]
            parts = first.strip().split(":", 1)
            if len(parts) == 2:
                return parts[1]
            return None
        except Exception:
            return None

    async def get_pane_pid(self, target: str) -> Optional[int]:
        """Return the foreground pane's pid for a given target (pane/window/session)."""
        try:
            out = await self._run_tmux_command(
                "display-message", "-p", "-t", target, "#{pane_pid}"
            )
            if out:
                try:
                    return int(out.strip())
                except ValueError:
                    return None
            return None
        except Exception:
            return None

    async def send_signal_to_pane(self, target: str, sig: int) -> bool:
        """Send a POSIX signal to the pane's foreground process group."""
        try:
            pid = await self.get_pane_pid(target)
            if not pid:
                return False
            pgid = os.getpgid(pid)
            os.killpg(pgid, sig)
            return True
        except Exception:
            return False

    async def get_pane_tty(self, target: str) -> Optional[str]:
        """Return the pane TTY path (e.g., /dev/ttys012) for the given target."""
        try:
            out = await self._run_tmux_command(
                "display-message", "-p", "-t", target, "#{pane_tty}"
            )
            return out.strip() if out else None
        except Exception:
            return None

    async def send_signal_to_foreground(self, target: str, sig: int) -> bool:
        """Send a signal to the terminal's foreground process group using TTY tcgetpgrp.

        This is the most accurate way to emulate Ctrl+C for interactive programs.
        """
        try:
            tty_path = await self.get_pane_tty(target)
            if not tty_path:
                return False
            fd = None
            try:
                flags = getattr(os, "O_RDWR", 2)
                # O_NOCTTY prevents this process from becoming the controlling terminal
                no_tty = getattr(os, "O_NOCTTY", 0)
                fd = os.open(tty_path, flags | no_tty)
                pgid = termios.tcgetpgrp(fd)
                if pgid > 0:
                    os.killpg(pgid, sig)
                    return True
                return False
            finally:
                if fd is not None:
                    try:
                        os.close(fd)
                    except Exception:
                        pass
        except Exception:
            return False
