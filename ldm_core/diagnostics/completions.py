import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from ldm_core.ui import UI
from ldm_core.utils import (
    get_actual_home,
    get_resource_path,
)


def is_completion_enabled(self):
    """Checks if completion setup is present in the user's shell profile."""
    home = get_actual_home()
    # Use SHELL if available, otherwise fallback to empty string
    raw_shell = os.environ.get("SHELL", "").lower()

    # Get just the binary name (e.g. /bin/zsh -> zsh)
    shell = raw_shell.split("/")[-1] if "/" in raw_shell else raw_shell
    if shell.endswith(".exe"):
        shell = shell[:-4]

    # Define profile files based on shell
    profiles = []
    if "zsh" in shell:
        profiles = [home / ".zshrc"]
    elif "bash" in shell:
        profiles = [home / ".bashrc", home / ".bash_profile", home / ".profile"]
    elif "fish" in shell:
        profiles = [home / ".config/fish/config.fish"]
    elif "powershell" in shell or "pwsh" in shell:
        profiles = [
            home / "Documents/PowerShell/Microsoft.PowerShell_profile.ps1",
            home / "Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1",
        ]

    # Look for the setup strings
    markers = ["ldm completion", "register-python-argcomplete ldm"]

    for profile in profiles:
        if profile.exists():
            try:
                content = profile.read_text()
                if any(marker in content for marker in markers):
                    return True
            except Exception:  # nosec B112
                continue

    return False

def _refresh_man_symlink(self):
    """Ensures a stable symlink for the man page exists in ~/.ldm/man/man1/."""
    if platform.system().lower() == "windows":
        return

    try:
        man_source = get_resource_path("ldm.1")
        if not man_source:
            return

        home = get_actual_home()
        man_dir = home / ".ldm" / "man" / "man1"
        man_dir.mkdir(parents=True, exist_ok=True)
        man_link = man_dir / "ldm.1"

        if man_link.is_symlink() or man_link.exists():
            man_link.unlink()

        man_link.symlink_to(man_source)
    except Exception:
        # Silent fail for symlink refresh
        pass

def run_completion(handler, target_shell=None):
    """Displays instructions or outputs shellcode for enabling completion."""
    # Detect active shell if not provided
    active_shell = os.environ.get("SHELL", "").split("/")[-1].lower()
    if active_shell.endswith(".exe"):
        active_shell = active_shell[:-4]

    # Normalize pwsh to powershell for internal logic
    if active_shell == "pwsh":
        active_shell = "powershell"

    # Refresh man symlink so 'man ldm' setup is always ready
    _refresh_man_symlink(handler)

    # If target_shell is specifically requested via CLI (e.g. 'ldm completion zsh')
    # we MUST only output shellcode to stdout to avoid breaking 'eval'.
    if target_shell:
        target_shell = target_shell.lower()
        try:
            import argcomplete

            if target_shell == "zsh":
                # We use the internal argcomplete shellcode generator
                code = argcomplete.shellcode(["ldm"], shell="zsh")  # nosec B604
                # Zsh requires compinit to support the 'compdef' command used by argcomplete
                print("# LDM Zsh Completion Initialization")
                print(
                    "(( $+functions[compdef] )) || { autoload -U compinit && compinit }"
                )
                print(code)
                return
            if target_shell == "bash":
                print(
                    argcomplete.shellcode(["ldm"], shell="bash")  # nosec B604
                )
                return
            if target_shell == "fish":
                print(
                    argcomplete.shellcode(["ldm"], shell="fish")  # nosec B604
                )
                return
            if target_shell == "powershell":
                # PowerShell doesn't have native argcomplete support, so we provide a bridge script
                print("# LDM PowerShell Completion Bridge")
                print(
                    "if (-not (Get-Command ldm -ErrorAction SilentlyContinue)) { return }"
                )
                print(" = {")
                print("    param(, , , , )")
                print("    $env:COMP_LINE = $commandAst.ToString()")
                print("    $env:COMP_POINT = $cursorPosition")
                print("    $env:_ARGCOMPLETE = 1")
                print("    $results = & ldm 2>$null")
                print("    $results | ForEach-Object {")
                print(
                    "        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)"
                )
                print("    }")
                print(
                    "    Remove-Item Env:COMP_LINE, Env:COMP_POINT, Env:_ARGCOMPLETE"
                )
                print("}")
                print(
                    "Register-ArgumentCompleter -Native -CommandName ldm -ScriptBlock $scriptblock"
                )
                return
        except Exception as e:
            # If generation fails, we print the error to stderr so eval ignores it
            print(f"Error generating completion: {e}", file=sys.stderr)
            return

    UI.heading("LDM Shell Completion")
    shell = active_shell
    if shell not in ["bash", "zsh", "fish", "powershell"]:
        UI.info(
            f"Completion is currently optimized for bash, zsh, fish, and powershell. (Found: {shell})"
        )
        return

    UI.info(
        f"To enable tab-completion for {UI.BYELLOW}{shell}{UI.COLOR_OFF}, add this to your startup profile:"
    )

    if shell == "zsh":
        print('\n    eval "$(ldm completion zsh)"\n')
        profile = ".zshrc"
    elif shell == "bash":
        print('\n    eval "$(ldm completion bash)"\n')
        profile = ".bashrc"
    elif shell == "fish":
        print("\n    ldm completion fish | source\n")
        profile = "config.fish"
    elif shell == "powershell":
        print("\n    ldm completion powershell | Out-String | Invoke-Expression\n")
        profile = "Microsoft.PowerShell_profile.ps1"

    UI.info(
        f"To support native {UI.BOLD}man ldm{UI.COLOR_OFF}, add this to the same file:"
    )
    print('\n    export MANPATH="$MANPATH:$HOME/.ldm/man"\n')

    UI.info(
        f"You may need to restart your terminal or source your profile ({UI.CYAN}~/{profile}{UI.COLOR_OFF})"
    )
    print("for the changes to take effect.")

def run_man(handler):
    """Displays the ldm manual page."""
    _refresh_man_symlink(handler)
    man_path = get_resource_path("ldm.1")
    if not man_path:
        UI.die("Manual page 'ldm.1' not found in resources.")

    # On macOS/Linux, we can use 'man -l' to view a local file
    # Fallback to 'less' if 'man' is not found or fails
    try:
        import subprocess

        if platform.system().lower() != "windows":
            # Check if man supports -l (macOS and most Linux)
            res = subprocess.run(
                ["man", "--help"], capture_output=True, text=True, check=False
            )
            if "-l" in res.stdout or "-l" in res.stderr:
                subprocess.run(["man", "-l", str(man_path)], check=False)
            # Fallback to less with roff processing if possible, or raw text
            # We can use mandoc or groff if available
            elif shutil.which("mandoc"):
                subprocess.run(
                    f"mandoc -Tutf8 {man_path} | less -R",
                    shell=True,  # nosec B602 B604
                    check=False,
                )
            elif shutil.which("groff"):
                subprocess.run(
                    f"groff -man -Tascii {man_path} | less -R",
                    shell=True,  # nosec B602 B604
                    check=False,
                )
            else:
                subprocess.run(["less", str(man_path)], check=False)
        else:
            # Windows fallback to notepad or similar
            subprocess.run(["notepad", str(man_path)], check=False)
    except Exception as e:
        UI.error(f"Failed to display manual: {e}")
        UI.info(f"You can view the raw manual file at: {man_path}")

def run_setup_completion(handler, target_shell=None):
    """Automates the setup of autocomplete for the detected or specified shell."""
    # 1. Verify/install argcomplete
    try:
        import argcomplete  # noqa: F401
    except ImportError:
        UI.info("Module 'argcomplete' not found. Attempting to install via pip...")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "argcomplete"],
                check=True,
                capture_output=True,
            )
            UI.success("Successfully installed 'argcomplete'!")
        except Exception as e:
            UI.warning(f"Could not auto-install 'argcomplete': {e}")
            UI.info("Please install it manually with: pip install argcomplete")

    # 2. Shell detection
    shell = target_shell
    if not shell:
        active_shell = os.environ.get("SHELL", "").split("/")[-1].lower()
        if active_shell.endswith(".exe"):
            active_shell = active_shell[:-4]
        if active_shell == "pwsh":
            active_shell = "powershell"

        if not active_shell:
            if "PSModulePath" in os.environ or sys.platform == "win32":
                active_shell = "powershell"
            else:
                active_shell = (
                    "zsh" if platform.system().lower() == "darwin" else "bash"
                )

        shell = active_shell

    shell = shell.lower()
    if shell not in ["bash", "zsh", "fish", "powershell"]:
        if sys.platform == "win32":
            shell = "powershell"
        elif platform.system().lower() == "darwin":
            shell = "zsh"
        else:
            shell = "bash"

    # 3. Determine profile path
    profile_path = None
    home = get_actual_home()

    if shell == "zsh":
        profile_path = home / ".zshrc"
    elif shell == "bash":
        bashrc = home / ".bashrc"
        bash_profile = home / ".bash_profile"
        if not bashrc.exists() and bash_profile.exists():
            profile_path = bash_profile
        else:
            profile_path = bashrc
    elif shell == "fish":
        profile_path = home / ".config" / "fish" / "config.fish"
    elif shell == "powershell":
        for ps_exe in ["pwsh", "powershell"]:
            try:
                res = subprocess.run(
                    [ps_exe, "-NoProfile", "-Command", "$PROFILE"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                out = res.stdout.strip()
                if out:
                    profile_path = Path(out)
                    break
            except Exception:
                continue

        if not profile_path:
            if sys.platform == "win32":
                profile_path = (
                    home
                    / "Documents"
                    / "PowerShell"
                    / "Microsoft.PowerShell_profile.ps1"
                )
            else:
                profile_path = (
                    home
                    / ".config"
                    / "powershell"
                    / "Microsoft.PowerShell_profile.ps1"
                )

    if not profile_path:
        UI.die(f"Could not determine the profile path for shell: {shell}")
        return

    # 4. Generate Autocomplete Content Block
    start_marker = "# >>> LDM CLI AUTOCOMPLETE >>>"
    end_marker = "# <<< LDM CLI AUTOCOMPLETE <<<"

    if shell == "zsh":
        inner_code = (
            "    # LDM Zsh Completion Initialization\n"
            "    (( $+functions[compdef] )) || { autoload -U compinit && compinit }\n"
            '    eval "$(ldm completion zsh)"'
        )
    elif shell == "bash":
        inner_code = '    eval "$(ldm completion bash)"'
    elif shell == "fish":
        inner_code = "    ldm completion fish | source"
    elif shell == "powershell":
        inner_code = (
            "    ldm completion powershell | Out-String | Invoke-Expression"
        )

    block_content = f"{start_marker}\n{inner_code}\n{end_marker}"

    # 5. Modify profile with safety checks & backups
    try:
        profile_path.parent.mkdir(parents=True, exist_ok=True)

        if profile_path.exists():
            bak_path = profile_path.with_suffix(profile_path.suffix + ".bak")
            import shutil

            shutil.copy2(profile_path, bak_path)
            UI.info(f"Created profile backup at: {bak_path}")
            content = profile_path.read_text(encoding="utf-8")
        else:
            content = ""

        if start_marker in content and end_marker in content:
            start_idx = content.find(start_marker)
            end_idx = content.find(end_marker) + len(end_marker)
            new_content = content[:start_idx] + block_content + content[end_idx:]
            UI.info(f"Updating existing LDM completion block in {profile_path}...")
        else:
            if content and not content.endswith("\n"):
                new_content = content + "\n\n" + block_content + "\n"
            else:
                new_content = content + "\n" + block_content + "\n"
            UI.info(f"Appending LDM completion block to {profile_path}...")

        profile_path.write_text(new_content, encoding="utf-8")
        UI.success(f"Autocomplete setup complete for {shell}!")
        UI.info(
            f"Please source your profile or restart your terminal: source {profile_path}"
        )

    except Exception as e:
        UI.die(f"Failed to setup completion in {profile_path}: {e}")

