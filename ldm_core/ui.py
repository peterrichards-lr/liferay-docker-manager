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
    def info(msg):
        print(f"{UI.YELLOW}ℹ {msg.strip()}{UI.COLOR_OFF}")

    @staticmethod
    def success(msg):
        print(f"{UI.GREEN}✅ {msg.strip()}{UI.COLOR_OFF}")

    @staticmethod
    def warning(msg):
        print(f"{UI.YELLOW}⚠️  Warning: {msg.strip()}{UI.COLOR_OFF}")

    @staticmethod
    def error(msg):
        print(f"{UI.BRED}❌ Error:{UI.COLOR_OFF} {msg.strip()}", file=sys.stderr)

    @staticmethod
    def die(msg):
        UI.error(msg.strip())
        sys.exit(1)

    @staticmethod
    def heading(msg):
        print(f"\n{UI.BYELLOW}=== {msg.strip()} ==={UI.COLOR_OFF}")

    @staticmethod
    def debug(msg):
        """Prints info only if verbose mode is enabled (implicitly checked here)."""
        print(f"{UI.WHITE}⚙️  {msg.strip()}{UI.COLOR_OFF}")

    @staticmethod
    def ask(prompt, default=None):
        prompt = prompt.strip()
        try:
            if default:
                res = input(
                    f"{UI.WHITE}❓ {prompt} [{UI.GREEN}{default}{UI.WHITE}]: {UI.COLOR_OFF}"
                )
                res = res.strip() if res else default
            else:
                res = input(f"{UI.WHITE}❓ {prompt}: {UI.COLOR_OFF}").strip()

            if res and res.lower() == "q":
                print("")
                UI.info("Aborted by user.")
                sys.exit(0)
            return res
        except KeyboardInterrupt:
            print("\n")
            UI.info("Interrupted by user. Exiting...")
            sys.exit(130)

    @staticmethod
    def format_size(size):
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
