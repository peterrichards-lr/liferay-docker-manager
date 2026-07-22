import contextlib
import os
import sys

if sys.platform == "win32":
    # Enable ANSI escape sequence processing in Windows consoles
    os.system("")

try:
    from ldm_core.ui_colors import UIColors
except ImportError:

    class UIColors:  # type: ignore[no-redef]
        COLOR_OFF = ""
        GREEN = ""
        YELLOW = ""
        WHITE = ""
        BWHITE = ""
        BYELLOW = ""
        RED = ""
        BRED = ""
        CYAN = ""
        BCYAN = ""
        BGREEN = ""
        BLUE = ""
        BOLD = ""
        DIM = ""
        UNDERLINE = ""


# --- UI Helpers ---
class UI:
    COLOR_OFF = UIColors.COLOR_OFF
    GREEN = UIColors.GREEN
    YELLOW = UIColors.YELLOW
    WHITE = UIColors.WHITE
    BWHITE = UIColors.BWHITE
    BYELLOW = UIColors.BYELLOW
    RED = UIColors.RED
    BRED = UIColors.BRED
    CYAN = UIColors.CYAN
    BCYAN = UIColors.BCYAN
    BGREEN = UIColors.BGREEN
    BLUE = UIColors.BLUE
    BOLD = UIColors.BOLD
    DIM = UIColors.DIM
    UNDERLINE = UIColors.UNDERLINE
    HIDDEN = "\033[8m"

    NON_INTERACTIVE = False
    VERBOSE = False
    INFO_MODE = False
    QUIET_MODE = False
    NO_COLOR = False
    NO_UNICODE = False

    TRACE_LOG_PATH = None
    _trace_handle = None

    @classmethod
    def reset(cls):
        """Resets mutable class-level properties to defaults for test isolation."""
        cls.NON_INTERACTIVE = False
        cls.VERBOSE = False
        cls.INFO_MODE = False
        cls.QUIET_MODE = False
        cls.NO_COLOR = False
        cls.NO_UNICODE = False
        cls.TRACE_LOG_PATH = None
        if cls._trace_handle:
            try:
                cls._trace_handle.close()
            except Exception:
                pass
            cls._trace_handle = None

    @classmethod
    @contextlib.contextmanager
    def patch(cls, non_interactive=None, verbose=None, info_mode=None, quiet_mode=None):
        """Context manager to temporarily modify UI settings and restore them."""
        old_ni = cls.NON_INTERACTIVE
        old_v = cls.VERBOSE
        old_info = cls.INFO_MODE
        old_quiet = cls.QUIET_MODE
        if non_interactive is not None:
            cls.NON_INTERACTIVE = non_interactive
        if verbose is not None:
            cls.VERBOSE = verbose
        if info_mode is not None:
            cls.INFO_MODE = info_mode
        if quiet_mode is not None:
            cls.QUIET_MODE = quiet_mode
        try:
            yield
        finally:
            cls.NON_INTERACTIVE = old_ni
            cls.VERBOSE = old_v
            cls.INFO_MODE = old_info
            cls.QUIET_MODE = old_quiet

    @staticmethod
    def init_trace_log(args):
        """Initializes the rolling trace log for debugging."""
        try:
            from datetime import datetime

            from ldm_core.utils import get_actual_home

            UI.TRACE_LOG_PATH = get_actual_home() / ".ldm" / "last-command.log"
            UI.TRACE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

            # Truncate on each new invocation
            UI._trace_handle = open(UI.TRACE_LOG_PATH, "w", encoding="utf-8")  # noqa: SIM115

            ts = datetime.now().isoformat()
            command_line = " ".join(args)
            UI._trace_handle.write(f"--- LDM Trace Log Started at {ts} ---\n")
            UI._trace_handle.write(f"Command: {command_line}\n")
            import platform

            UI._trace_handle.write(f"Platform: {platform.platform()}\n")
            UI._trace_handle.write(f"Python: {sys.version.split()[0]}\n")
            UI._trace_handle.write("-" * 50 + "\n")
            UI._trace_handle.flush()
        except Exception:
            # Silently ignore trace log failures
            UI._trace_handle = None

    @staticmethod
    def trace(msg):
        """Writes a message strictly to the trace log, silently."""
        if UI._trace_handle:
            try:
                import re

                clean_msg = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", str(msg))
                UI._trace_handle.write(clean_msg + "\n")
                UI._trace_handle.flush()
            except Exception:
                pass

    @staticmethod
    def get_padding(icon=None):
        """Returns the OS-aware padding for icons."""
        # Standardized 2-space padding for all platforms
        return "  "

    @staticmethod
    def redact(text):
        """Redacts sensitive patterns (passwords, tokens, keys) from a string."""
        if not text:
            return text

        import re

        # 1. Redact KEY=VALUE patterns (e.g. MYSQL_PASSWORD=secret)
        keys = ["PASSWORD", "SECRET", "TOKEN", "KEY", "AUTH"]
        kv_pattern = r"(?i)(" + "|".join(keys) + r")=([^&\s]+)"
        text = re.sub(kv_pattern, r"\1=[REDACTED]", str(text))

        # 2. Redact CLI password flags (e.g. -pPASSWORD or --password=secret)
        # Avoid matching '-pre' (our version suffix)
        flag_pattern = r"(?i)(--password=)([^&\s]+)"
        text = re.sub(flag_pattern, r"\1[REDACTED]", text)

        # Explicitly handle MySQL -p flag (-pSecret) without matching -pre
        p_pattern = r"(?i)(\s-p)(?!re\.)([^&\s]+)"
        return re.sub(p_pattern, r"\1[REDACTED]", text)

    @staticmethod
    def _print(msg, color=None, icon=None, file=None):
        """Internal print helper with Unicode safety and automatic redaction."""
        if file is None:
            file = sys.stdout

        # Clean and Redact at the sink (Defensive Layer)
        msg = UI.redact(msg.strip())

        # Format the output string
        padding = UI.get_padding(icon)
        out = f"{icon}{padding}{msg}" if icon else msg
        if color and not getattr(UI, "NO_COLOR", False):
            out = f"{color}{out}{UI.COLOR_OFF}"

        # Write to the global trace log cleanly
        UI.trace(out)

        if getattr(UI, "NO_COLOR", False):
            import re

            ansi_escape = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
            out = ansi_escape.sub("", out)

        if getattr(UI, "NO_UNICODE", False):
            safe_out = (
                out.replace("✅", "[OK]")
                .replace("❌", "[X]")
                .replace("⚠️", "[!]")
                .replace("ℹ", "[i]")
                .replace("●", "*")
                .replace("○", "o")
                .replace("×", "x")
                .replace("└─", "  +-")
                .replace("❓", "[?]")
            )
            safe_out = safe_out.encode("ascii", "replace").decode("ascii")
            print(safe_out, file=file, flush=True)
            return

        try:
            # Test if the output can be encoded in the target file encoding
            if (
                hasattr(file, "encoding")
                and isinstance(file.encoding, str)
                and file.encoding
            ):
                out.encode(file.encoding)
            # Try printing with the current encoding
            print(out, file=file, flush=True)  # fmt: skip
        except (UnicodeEncodeError, OSError):
            # Fallback for old Windows consoles (CP1252) or problematic streams
            # Replace known problematic symbols with ASCII equivalents
            safe_out = (
                out.replace("✅", "[OK]")
                .replace("❌", "[X]")
                .replace("⚠️", "[!]")
                .replace("ℹ", "[i]")
                .replace("●", "*")
                .replace("○", "o")
                .replace("×", "x")
                .replace("└─", "  +-")
                .replace("❓", "[?]")
            )
            # Final safety wash
            safe_out = safe_out.encode("ascii", "replace").decode("ascii")
            print(safe_out, file=file, flush=True)  # fmt: skip

    class Spinner:
        """A simple animated spinner context manager."""

        def __init__(self, message="Waiting..."):
            self.message = message
            self.is_running = False
            self.thread = None
            if getattr(UI, "NO_UNICODE", False):
                self.frames = ["|", "/", "-", "\\"]
            else:
                self.frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

        def _spin(self):
            import shutil
            import sys
            import time

            i = 0
            while self.is_running:
                # Use terminal width to ensure the line is fully cleared
                try:
                    columns, _ = shutil.get_terminal_size()
                except Exception:
                    columns = 80

                # Ensure we don't exceed terminal width with the message
                # We use a 15-char margin to be extremely safe with colors/frames
                msg = self.message
                if len(msg) > (columns - 15):
                    truncated = msg[: (columns - 18)]
                    # Heuristic: Try to snap to the last whitespace to avoid cutting words
                    last_space = truncated.rfind(" ")
                    if last_space > (columns // 2):
                        msg = truncated[:last_space] + "..."
                    else:
                        msg = truncated + "..."

                # Write frame and message
                # \r moves to start of line
                # \033[K clears from cursor to end of line (ANSI standard)
                # We write \033[K twice (start and after msg) for maximum compatibility
                frame = self.frames[i]
                color_start = UI.CYAN if not getattr(UI, "NO_COLOR", False) else ""
                color_end = UI.COLOR_OFF if not getattr(UI, "NO_COLOR", False) else ""
                sys.stdout.write(
                    f"\r\033[K  {color_start}{frame}{color_end}  {msg}\033[K"
                )
                sys.stdout.flush()
                time.sleep(0.1)
                i = (i + 1) % len(self.frames)

            # Clear line when done
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()

        def update(self, message):
            """Updates the message displayed next to the spinner."""
            self.message = message

        def __enter__(self):
            if getattr(UI, "NON_INTERACTIVE", False) or not sys.stdout.isatty():
                UI.detail(self.message)
                return self

            import threading

            self.is_running = True
            self.thread = threading.Thread(target=self._spin, daemon=True)
            self.thread.start()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.is_running = False
            if self.thread:
                self.thread.join()

    @staticmethod
    def spinner(message="Waiting..."):
        return UI.Spinner(message)

    @staticmethod
    def print_banner():
        """Prints a stylish ASCII banner for LDM initialization."""
        banner = rf"""{UI.CYAN}
    __    ____  __  ___
   / /   / __ \/  |/  /
  / /   / / / / /|_/ /
 / /___/ /_/ / /  / /
/_____/_____/_/  /_/
{UI.BOLD}Liferay Docker Manager{UI.COLOR_OFF}
"""
        print(banner)

    @staticmethod
    def get_beta_label(version):
        """Returns a stylized BETA tag if the version contains a hyphen (pre-release)."""
        if "-" in version:
            return f" {UI.BLUE}{UI.BOLD}[BETA]{UI.COLOR_OFF}"
        return ""

    @staticmethod
    def table(rows, headers=None):
        """Prints a sleek, Unicode-based table with full separators."""
        if not rows:
            return

        # Calculate column widths
        num_cols = len(rows[0])
        col_widths = [0] * num_cols

        all_data = ([headers] if headers else []) + rows
        import re
        import unicodedata

        def get_display_width(s):
            clean_s = re.sub(r"\x1b\[[0-9;]*m", "", str(s))
            return sum(
                2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
                for c in clean_s
            )

        for row in all_data:
            for i, val in enumerate(row):
                col_widths[i] = max(col_widths[i], get_display_width(val))

        # Build Borders
        def get_line(left, middle, right, cross):
            line = left
            for i, w in enumerate(col_widths):
                line += middle * (w + 2)
                if i < num_cols - 1:
                    line += cross
            return line + right

        top = get_line("╭", "─", "╮", "┬")
        bottom = get_line("╰", "─", "╯", "┴")
        sep = get_line("├", "─", "┤", "┼")

        UI.raw(f"{UI.DIM}{top}{UI.COLOR_OFF}")

        if headers:
            head_str = "│ "
            for i, h in enumerate(headers):
                pad_len = col_widths[i] - get_display_width(h)
                head_str += f"{UI.WHITE}{UI.BOLD}{h!s}{' ' * pad_len}{UI.COLOR_OFF} │ "
            UI.raw(head_str)
            UI.raw(f"{UI.DIM}{sep}{UI.COLOR_OFF}")

        for row in rows:
            row_str = "│ "
            for i, val in enumerate(row):
                # We have to account for visual width (emojis) and ANSI colors
                pad_len = col_widths[i] - get_display_width(val)
                row_str += f"{val}{' ' * pad_len} │ "
            UI.raw(row_str)

        UI.raw(f"{UI.DIM}{bottom}{UI.COLOR_OFF}")

    @staticmethod
    def raw(msg):
        """Prints a raw string through the safety/redaction layer."""
        UI._print(msg)

    @staticmethod
    def info(msg):
        if not UI.QUIET_MODE:
            UI._print(msg, UI.YELLOW, "ℹ")

    @staticmethod
    def detail(msg):
        """Prints info only if info mode or verbose mode is enabled (middle tier)."""
        if UI.INFO_MODE or UI.VERBOSE:
            UI._print(msg, UI.YELLOW, "ℹ")

    @staticmethod
    def success(msg):
        UI._print(msg, UI.GREEN, "✅")

    @staticmethod
    def warning(msg):
        UI._print(msg, UI.YELLOW, "⚠️")

    @staticmethod
    def error(msg, details=None, tip=None):
        UI._print(msg, UI.BRED, "❌", file=sys.stderr)
        if details:
            # Redact and safely print details
            redacted_details = UI.redact(str(details))
            UI._print(f"Details:  {redacted_details}", color=UI.WHITE, file=sys.stderr)
        if tip:
            UI._print(f"💡 Tip:      {tip}", color=UI.CYAN, file=sys.stderr)

    @staticmethod
    def die(msg, details=None, tip=None, exit_code=1):
        UI.error(msg, details, tip)
        sys.exit(exit_code)

    @staticmethod
    def heading(msg):
        # Redact headers just in case (e.g. project names containing sensitive words)
        msg = UI.redact(msg.strip())
        UI._print(f"\n=== {msg} ===", color=UI.BYELLOW)

    @staticmethod
    def phase(current: int, total: int, label: str):
        """Prints a phase progress header for multi-step commands."""
        if not UI.QUIET_MODE:
            marker = f"[{current}/{total}]"
            UI._print(f"{marker} {label}", color=UI.BCYAN)

    @staticmethod
    def debug(msg):
        """Prints info only if verbose mode is enabled."""
        if UI.VERBOSE:
            UI._print(msg, UI.WHITE, "⚙️")

    @staticmethod
    def ask_choices(prompt, choices, default=None):
        """Prompts the user to select from a list of predefined choices."""
        if UI.NON_INTERACTIVE:
            return default

        # Create a display string for choices, highlighting the default
        choice_display = []
        for c in choices:
            if c == default:
                choice_display.append(f"{UI.GREEN}{c}{UI.COLOR_OFF}")
            else:
                choice_display.append(c)

        choice_str = "/".join(choice_display)
        full_prompt = f"{prompt} ({choice_str})"

        while True:
            res = UI.ask(full_prompt, default)
            if not res and default:
                return default
            if res in choices:
                return res
            UI.warning(
                f"Invalid choice: {UI.CYAN}{res}{UI.COLOR_OFF}. Please select from: {', '.join(choices)}"
            )

    @staticmethod
    def ask(prompt, default=None):
        if UI.NON_INTERACTIVE:
            return default

        prompt = prompt.strip()
        try:
            if sys.platform == "win32":
                # Format a clean ASCII prompt for Windows Console host to avoid encoding issues and hangs
                padding = UI.get_padding()
                safe_prompt = f"[?]{padding}{prompt}"
                if default:
                    safe_prompt += f" [{default}]: "
                else:
                    safe_prompt += ": "

                try:
                    safe_prompt = safe_prompt.encode("ascii", "replace").decode("ascii")
                except Exception:
                    safe_prompt = (
                        f"? {prompt} [{default}]: " if default else f"? {prompt}: "
                    )

                from ldm_core.utils import Benchmarker

                with Benchmarker.measure_human():
                    try:
                        res = input(safe_prompt)
                    except (EOFError, KeyboardInterrupt):
                        raise
                    except Exception:
                        # In case of any unexpected console read errors, fall back to input() without prompt
                        res = input()
                return res.strip() if res else default

            icon = "[?]" if getattr(UI, "NO_UNICODE", False) else "❓"
            padding = UI.get_padding(icon)
            formatted_prompt = f"{UI.WHITE}{icon}{padding}{prompt}"
            if default:
                formatted_prompt += f" [{UI.GREEN}{default}{UI.WHITE}]: {UI.COLOR_OFF}"
            else:
                formatted_prompt += f": {UI.COLOR_OFF}"

            if getattr(UI, "NO_COLOR", False):
                import re

                ansi_escape = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
                formatted_prompt = ansi_escape.sub("", formatted_prompt)

            if getattr(UI, "NO_UNICODE", False):
                # If unicode is disabled, we must safe-wash the prompt as ASCII
                formatted_prompt = formatted_prompt.encode("ascii", "replace").decode(
                    "ascii"
                )

            try:
                sys.stdout.write(formatted_prompt)
                sys.stdout.flush()
            except (UnicodeEncodeError, OSError):
                # Fallback for Windows CP1252/piped input encoding limitations
                safe_prompt = f"{UI.WHITE}[?]{padding}{prompt}"
                if default:
                    safe_prompt += f" [{UI.GREEN}{default}{UI.WHITE}]: {UI.COLOR_OFF}"
                else:
                    safe_prompt += f": {UI.COLOR_OFF}"
                # Final safety wash to strip non-ascii if needed
                safe_prompt = safe_prompt.encode("ascii", "replace").decode("ascii")
                sys.stdout.write(safe_prompt)
                sys.stdout.flush()

            from ldm_core.utils import Benchmarker

            with Benchmarker.measure_human():
                res = input()

            return res.strip() if res else default
        except (EOFError, KeyboardInterrupt):
            print(f"\n{UI.WHITE}Aborted.{UI.COLOR_OFF}")
            sys.exit(130)

    @staticmethod
    def confirm(prompt, default="Y"):
        """Standardized Yes/No confirmation prompt."""
        if isinstance(default, bool):
            default_str = "Y" if default else "N"
        else:
            default_str = str(default).upper()
            if default_str not in ["Y", "N"]:
                default_str = "Y"

        prompt_choices = "Y/n" if default_str == "Y" else "y/N"
        res = UI.ask(prompt, prompt_choices)

        if not res or res == prompt_choices:
            return default_str == "Y"
        return res.lower().startswith("y")

    @staticmethod
    def format_size(size):
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    @staticmethod
    def format_duration(seconds):
        """Formats seconds into a human-readable duration (e.g. 1m 30s)."""
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"

        minutes = seconds // 60
        remaining_seconds = seconds % 60

        if minutes < 60:
            return f"{minutes}m {remaining_seconds}s"

        hours = minutes // 60
        remaining_minutes = minutes % 60
        return f"{hours}h {remaining_minutes}m {remaining_seconds}s"

    @staticmethod
    def interruptible_pause(seconds: int, message: str = "Press CTRL+C to cancel "):
        """Pauses execution while printing dots, gracefully allowing KeyboardInterrupt."""
        if UI.NON_INTERACTIVE or os.environ.get("PYTEST_CURRENT_TEST"):
            return

        import time

        sys.stdout.write(message)
        try:
            for _ in range(seconds):
                sys.stdout.write(".")
                sys.stdout.flush()
                time.sleep(1)
            sys.stdout.write("\n")
        except KeyboardInterrupt:
            sys.stdout.write("\n")
            raise
