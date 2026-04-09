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

    @staticmethod
    def _print(msg, color=None, icon=None, file=sys.stdout):
        """Internal print helper with Unicode safety."""
        # Clean the message
        msg = msg.strip()

        # Format the output string
        out = f"{icon} {msg}" if icon else msg
        if color:
            out = f"{color}{out}{UI.COLOR_OFF}"

        try:
            # Try printing with the current encoding
            print(out, file=file)
        except UnicodeEncodeError:
            # Fallback for old Windows consoles (CP1252)
            # Replace problematic characters with safe ASCII equivalents
            safe_out = out.encode("ascii", "replace").decode("ascii")
            print(safe_out, file=file)

    @staticmethod
    def info(msg):
        UI._print(msg, UI.YELLOW, "ℹ")

    @staticmethod
    def success(msg):
        UI._print(msg, UI.GREEN, "✅")

    @staticmethod
    def warning(msg):
        UI._print(msg, UI.YELLOW, "⚠️  Warning:")

    @staticmethod
    def error(msg, details=None):
        UI._print(msg, UI.BRED, "❌ Error:", file=sys.stderr)
        if details:
            print(f"{UI.WHITE}Details:{UI.COLOR_OFF} {details}", file=sys.stderr)

    @staticmethod
    def die(msg, details=None):
        UI.error(msg, details)
        sys.exit(1)

    @staticmethod
    def heading(msg):
        msg = msg.strip()
        print(f"\n{UI.BYELLOW}=== {msg} ==={UI.COLOR_OFF}")

    @staticmethod
    def debug(msg):
        """Prints info only if verbose mode is enabled."""
        UI._print(msg, UI.WHITE, "⚙️")

    @staticmethod
    def ask(prompt, default=None):
        prompt = prompt.strip()
        icon = "❓"
        try:
            formatted_prompt = f"{UI.WHITE}{icon} {prompt}"
            if default:
                formatted_prompt += f" [{UI.GREEN}{default}{UI.WHITE}]: {UI.COLOR_OFF}"
            else:
                formatted_prompt += f": {UI.COLOR_OFF}"

            try:
                res = input(formatted_prompt)
            except UnicodeEncodeError:
                # Fallback prompt for CP1252 (Standard Windows CMD)
                res = input(f"? {prompt} [{default}]: " if default else f"? {prompt}: ")

            return res.strip() if res else default
        except (EOFError, KeyboardInterrupt):
            print(f"\n{UI.WHITE}Aborted.{UI.COLOR_OFF}")
            sys.exit(130)

    @staticmethod
    def format_size(size):
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"
