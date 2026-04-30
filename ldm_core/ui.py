import sys


# --- UI Helpers ---
class UI:
    COLOR_OFF = "\033[0m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[0;33m"
    WHITE = "\033[0;37m"
    BYELLOW = "\033[1;33m"
    RED = "\033[0;31m"
    BRED = "\033[1;31m"
    CYAN = "\033[0;36m"
    BLUE = "\033[0;34m"
    BOLD = "\033[1m"

    NON_INTERACTIVE = False
    VERBOSE = False

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
        # We are careful to only match -p if followed by characters (not just a space)
        flag_pattern = r"(?i)(-p|--password=)([^&\s]+)"
        text = re.sub(flag_pattern, r"\1[REDACTED]", text)

        return text

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
        if color:
            out = f"{color}{out}{UI.COLOR_OFF}"

        try:
            # Try printing with the current encoding
            print(out, file=file, flush=True)
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
            print(safe_out, file=file, flush=True)

    @staticmethod
    def get_beta_label(version):
        """Returns a stylized BETA tag if the version contains a hyphen (pre-release)."""
        if "-" in version:
            return f" {UI.BLUE}{UI.BOLD}[BETA]{UI.COLOR_OFF}"
        return ""

    @staticmethod
    def raw(msg):
        """Prints a raw string through the safety/redaction layer."""
        UI._print(msg)

    @staticmethod
    def info(msg):
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
    def die(msg, details=None, tip=None):
        UI.error(msg, details, tip)
        if details and not isinstance(details, str):
            import traceback

            UI.raw(f"\n{UI.WHITE}--- Debug Traceback ---{UI.COLOR_OFF}")
            traceback.print_exc()
        sys.exit(1)

    @staticmethod
    def heading(msg):
        # Redact headers just in case (e.g. project names containing sensitive words)
        msg = UI.redact(msg.strip())
        UI._print(f"\n=== {msg} ===", color=UI.BYELLOW)

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
        icon = "❓"
        padding = UI.get_padding(icon)
        try:
            formatted_prompt = f"{UI.WHITE}{icon}{padding}{prompt}"
            if default:
                formatted_prompt += f" [{UI.GREEN}{default}{UI.WHITE}]: {UI.COLOR_OFF}"
            else:
                formatted_prompt += f": {UI.COLOR_OFF}"

            sys.stdout.write(formatted_prompt)
            sys.stdout.flush()

            try:
                res = input()
            except UnicodeEncodeError:
                # Fallback prompt for CP1252 (Standard Windows CMD)
                res = input(f"? {prompt} [{default}]: " if default else f"? {prompt}: ")

            return res.strip() if res else default
        except (EOFError, KeyboardInterrupt):
            print(f"\n{UI.WHITE}Aborted.{UI.COLOR_OFF}")
            sys.exit(130)

    @staticmethod
    def confirm(prompt, default="Y"):
        """Standardized Yes/No confirmation prompt."""
        res = UI.ask(prompt, default)
        return str(res).lower() == "y"

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
