import argparse
import platform
import signal
import sys
import warnings

if sys.version_info < (3, 10):  # noqa: UP036
    sys.stderr.write(
        f"Error: LDM requires Python 3.10 or higher.\n"
        f"Current Python version is: {platform.python_version()}\n\n"
        f"Please install Python 3.10+ (via Homebrew or python.org) and run LDM using a newer Python interpreter:\n"
        f"  python3.12 /usr/local/bin/ldm [args]\n"
    )
    sys.exit(1)


# --- SIGINT / CTRL+C Graceful Exit Handler ---
def _graceful_exit(sig, frame):
    sys.stdout.write("\n\r")
    sys.exit(0)


signal.signal(signal.SIGINT, _graceful_exit)


from ldm_core.constants import SCRIPT_DIR, VERSION  # noqa: E402
from ldm_core.manager import LiferayManager  # noqa: E402
from ldm_core.ui import UI  # noqa: E402
from ldm_core.utils import check_for_updates  # noqa: E402

try:
    import argcomplete
except ImportError:
    argcomplete = None  # type: ignore


def project_completer(prefix, **kwargs):
    from ldm_core.utils import find_dxp_roots

    roots = find_dxp_roots()
    return [r["path"].name for r in roots if r["path"].name.startswith(prefix)]


def preprocess_args(args_list: list[str]) -> list[str]:
    if not args_list:
        return args_list

    # Determine if args_list has a program/executable name at index 0.
    # If args_list[0] starts with '-' or is a known command/namespace, it does not have a prog name.
    has_prog = True
    first = args_list[0]

    all_cmds = {
        "infra-setup",
        "infra-down",
        "infra-restart",
        "init-common",
        "renew-ssl",
        "migrate-search",
        "cloud-fetch",
        "defaults",
        "env",
        "feature",
        "log-level",
        "edit",
        "prune",
        "doctor",
        "upgrade",
        "version",
        "dev-setup",
        "completion",
        "setup-completion",
        "quickstart",
        "man",
        "fix-hosts",
        "config",
        "run",
        "up",
        "import",
        "hydrate",
        "init",
        "init-from",
        "monitor",
        "stop",
        "restart",
        "down",
        "rm",
        "logs",
        "deploy",
        "reindex",
        "snapshot",
        "restore",
        "info",
        "reset",
        "re-seed",
        "cache",
        "clear-cache",
        "clear-tags",
        "shell",
        "gogo",
        "browser",
        "open",
        "scale",
        "wait",
        "status",
        "ps",
        "list",
        "ls",
        "infra",
        "cloud",
        "system",
        "share",
    }

    if first.startswith("-") or first in all_cmds:
        has_prog = False

    # Prepend dummy prog name if not present
    processed_list = args_list if has_prog else ["ldm", *args_list]

    cmd_idx = -1
    for i in range(1, len(processed_list)):
        if not processed_list[i].startswith("-"):
            cmd_idx = i
            break

    if cmd_idx != -1:
        cmd = processed_list[cmd_idx]

        # Map of legacy flat commands to (namespace, subcommand)
        legacy_map = {
            "infra-setup": ("infra", "setup"),
            "infra-down": ("infra", "down"),
            "infra-restart": ("infra", "restart"),
            "init-common": ("infra", "init-common"),
            "renew-ssl": ("infra", "renew-ssl"),
            "migrate-search": ("infra", "migrate-search"),
            "cloud-fetch": ("cloud", "fetch"),
            "defaults": ("config", "defaults"),
            "env": ("config", "env"),
            "feature": ("config", "feature"),
            "log-level": ("config", "log-level"),
            "edit": ("config", "edit"),
            "prune": ("system", "prune"),
            "doctor": ("system", "doctor"),
            "upgrade": ("system", "upgrade"),
            "version": ("system", "version"),
            "dev-setup": ("system", "dev-setup"),
            "completion": ("system", "completion"),
            "setup-completion": ("system", "setup-completion"),
            "man": ("system", "man"),
            "fix-hosts": ("system", "fix-hosts"),
        }

        if cmd in legacy_map:
            ns, subcmd = legacy_map[cmd]
            new_args = list(processed_list)
            new_args[cmd_idx : cmd_idx + 1] = [ns, subcmd]
            processed_list = new_args
        elif cmd == "config":
            # Check if the next argument is a known subcommand
            subcmds = [
                "get",
                "set",
                "remove",
                "defaults",
                "env",
                "feature",
                "log-level",
                "edit",
            ]
            if (
                cmd_idx + 1 < len(processed_list)
                and processed_list[cmd_idx + 1] in subcmds
            ):
                pass
            else:
                # Legacy config processing
                remaining = processed_list[cmd_idx + 1 :]
                remove_present = False
                non_flag_args = []
                other_flags = []

                for arg in remaining:
                    if arg == "--remove":
                        remove_present = True
                    elif arg.startswith("-"):
                        other_flags.append(arg)
                    else:
                        non_flag_args.append(arg)

                new_args = processed_list[:cmd_idx]
                if remove_present:
                    new_args += ["config", "remove"]
                    if non_flag_args:
                        new_args.append(non_flag_args[0])
                    new_args += other_flags
                    if len(non_flag_args) > 1:
                        new_args += non_flag_args[1:]
                elif len(non_flag_args) >= 2:
                    new_args += [
                        "config",
                        "set",
                        non_flag_args[0],
                        non_flag_args[1],
                        *non_flag_args[2:],
                        *other_flags,
                    ]
                elif len(non_flag_args) == 1:
                    new_args += ["config", "get", non_flag_args[0], *other_flags]
                else:
                    new_args += ["config", "get", *other_flags]

                processed_list = new_args

    if not has_prog:
        processed_list = processed_list[1:]

    return processed_list


def get_parser():
    # Define a parent parser for common arguments shared by all subparsers
    # This allows flags like -v and -y to be placed both before AND after subcommands
    base_parent = argparse.ArgumentParser(add_help=False)
    base_parent.add_argument(
        "--info",
        action="store_true",
        help="Show informational logging (middle tier)",
    )
    base_parent.add_argument("-v", "--verbose", action="store_true")
    base_parent.add_argument("-y", "--non-interactive", action="store_true")
    base_parent.add_argument(
        "-q", "--quiet", action="store_true", help="Quiet mode (suppress info logs)"
    )
    base_parent.add_argument(
        "--dry-run", action="store_true", help="Preview execution without mutations"
    )
    base_parent.add_argument(
        "--benchmark", action="store_true", help="Display performance benchmark"
    )
    base_parent.add_argument(
        "--overwrite-registry",
        action="store_true",
        help="Automatically overwrite existing project registry entries in case of collisions",
    )

    # For subparsers, we want the global flags but we SUPPRESS the default (False)
    # so they don't overwrite the value set by the main parser if provided before the command.
    base_sub_parent = argparse.ArgumentParser(
        add_help=False, argument_default=argparse.SUPPRESS
    )
    base_sub_parent.add_argument("--info", action="store_true")
    base_sub_parent.add_argument("-v", "--verbose", action="store_true")
    base_sub_parent.add_argument("-y", "--non-interactive", action="store_true")
    base_sub_parent.add_argument("-q", "--quiet", action="store_true")
    base_sub_parent.add_argument("--dry-run", action="store_true")
    base_sub_parent.add_argument("--benchmark", action="store_true")
    base_sub_parent.add_argument("--overwrite-registry", action="store_true")

    parser = argparse.ArgumentParser(
        prog="ldm",
        description=f"Liferay Docker Manager (ldm) v{VERSION}",
        parents=[base_sub_parent],
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command")

    # Command: run (alias: up)
    run = subparsers.add_parser("run", aliases=["up"], parents=[base_sub_parent])
    run.add_argument("project", nargs="?")
    run.add_argument("-t", "--tag")
    run.add_argument(
        "--tag-latest",
        action="store_true",
        help="Automatically use the latest Liferay tag",
    )
    run.add_argument("--tag-prefix")
    run.add_argument("-p", "--project", dest="project_flag")
    run.add_argument("-c", "--container")
    run.add_argument("--host-name")
    run.add_argument("--ssl", action="store_true", default=None)
    run.add_argument("--no-ssl", action="store_false", dest="ssl")
    run.add_argument("--force-ssl", action="store_true")
    run.add_argument("--port", type=int)
    run.add_argument("--db", choices=["postgresql", "mysql", "hypersonic"])
    run.add_argument("--release-type", choices=["any", "u", "lts", "qr"])
    run.add_argument("--portal", action="store_true")
    run.add_argument("--refresh", action="store_true")
    run.add_argument(
        "--sidecar",
        action="store_true",
        help="Use internal Liferay Sidecar search instead of the shared Global Search container",
    )
    run.add_argument(
        "--es7", action="store_true", help="Use Elasticsearch 7 for global search"
    )
    run.add_argument("--no-up", action="store_true")
    run.add_argument("--no-wait", action="store_true")
    run.add_argument("--mount-logs", action="store_true")
    run.add_argument("--gogo-port", type=int)
    run.add_argument("--jvm-args", help="Override Liferay JVM arguments")
    run.add_argument(
        "--reindex",
        action="store_true",
        help="Force a full search reindex on startup",
    )
    run.add_argument(
        "--no-vol-cache",
        action="store_true",
        help="Disable :cached volumes on macOS/Windows",
    )
    run.add_argument(
        "--internal-state",
        action="store_true",
        help="Use internal anonymous volume for OSGi state (fixes locking issues on external drives)",
    )
    run.add_argument(
        "--no-jvm-verify",
        action="store_true",
        help="Disable JVM bytecode verification skip",
    )
    run.add_argument(
        "--no-tld-skip", action="store_true", help="Disable Tomcat TLD scanning skip"
    )
    run.add_argument(
        "--no-seed", action="store_true", help="Disable automatic project seeding"
    )
    run.add_argument(
        "--no-osgi-seed",
        action="store_true",
        help="Skip seeding the OSGi state folder (use when re-calculating bundles)",
    )
    run.add_argument(
        "--no-captcha",
        action="store_true",
        help="Disable CAPTCHA enforcement for Omni-Admin actions (testing only)",
    )
    run.add_argument(
        "--fast-login",
        action="store_true",
        help="Bypass typical startup prompts (terms of use, password reset) - best with external DBs",
    )
    run.add_argument(
        "--feature",
        nargs="+",
        help="Enable one or more Liferay feature flags (e.g. LPS-122920)",
    )
    run.add_argument("-f", "--follow", action="store_true")
    run.add_argument("--env", action="append")
    run.add_argument(
        "--samples",
        action="store_true",
        help="Initialize with sample client extensions",
    )
    run.add_argument(
        "--snapshot",
        help="Initialize project from an external snapshot folder",
    )
    run.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Maximum time to wait for health (default: 900)",
    )
    run.add_argument(
        "--lean",
        action="store_true",
        help="Use a resource-optimized JVM profile (useful for CI or low-memory systems)",
    )
    run.add_argument(
        "--open",
        action="store_true",
        help="Automatically open Liferay in browser after starting",
    )
    run.add_argument(
        "--scale",
        nargs="+",
        dest="scale_list",
        help="Scale services (e.g. --scale liferay=2)",
    )
    run.add_argument(
        "--expose",
        action="store_true",
        help="Start an ngrok container to expose Liferay to the public internet",
    )
    run.add_argument(
        "--share",
        action="store_true",
        help="Automatically start a secure tunnel (lfr-tunnel) to share the instance",
    )
    run.add_argument(
        "--share-subdomain",
        help="Custom subdomain to use when sharing the instance",
    )
    run.add_argument(
        "--share-provider",
        choices=["lfr-tunnel", "lfr-tunnel-docker", "ngrok"],
        help="Sharing provider to use (defaults to lfr-tunnel)",
    )
    run.add_argument(
        "--share-image",
        help="Custom Docker image to use for the sharing tunnel sidecar (defaults to peterjrichards/lfr-tunnel:latest)",
    )
    run.add_argument(
        "--share-inspector",
        action="store_true",
        help="Expose the lfr-tunnel local inspector dashboard on port 4040",
    )
    run.add_argument(
        "--share-domain",
        help="Custom domain to use when sharing the instance (e.g. lfr-demo.online, lfr-demo.se)",
    )
    run.add_argument(
        "--auto-install-lfr-tunnel",
        action="store_true",
        help="Automatically install lfr-tunnel if not found in PATH",
    )
    run.add_argument(
        "--persist-osgi",
        action="store_true",
        default=None,
        help="Persist the OSGi state folder across container restarts",
    )
    run.add_argument(
        "--no-persist-osgi",
        action="store_false",
        dest="persist_osgi",
        help="Do not persist the OSGi state folder",
    )

    # Command: import
    imp = subparsers.add_parser("import", parents=[base_sub_parent])
    imp.add_argument("source")
    imp.add_argument("project", nargs="?")
    imp.add_argument("-p", "--project", dest="project_flag")
    imp.add_argument("--cloud-project", help="Liferay Cloud project ID")
    imp.add_argument("--target-env", default="local")
    imp.add_argument(
        "--hydrate-from",
        help="Automatically hydrate data from a Liferay Cloud environment",
    )
    imp.add_argument(
        "--no-env-sync",
        action="store_true",
        help="Skip syncing environment variables from Liferay Cloud",
    )
    imp.add_argument("--no-run", action="store_true")
    imp.add_argument(
        "--stop-running",
        action="store_true",
        help="Automatically stop the project if it is currently running",
    )
    imp.add_argument(
        "--leave-running",
        action="store_true",
        help="Keep the running project active and abort the import if it is currently running",
    )
    imp.add_argument(
        "--share",
        action="store_true",
        help="Automatically start a secure tunnel (lfr-tunnel) to share the instance after import",
    )
    imp.add_argument(
        "--share-subdomain",
        help="Custom subdomain to use when sharing the instance",
    )
    imp.add_argument(
        "--share-provider",
        choices=["lfr-tunnel", "lfr-tunnel-docker", "ngrok"],
        help="Sharing provider to use (defaults to lfr-tunnel)",
    )
    imp.add_argument(
        "--share-image",
        help="Custom Docker image to use for the sharing tunnel sidecar",
    )
    imp.add_argument(
        "--share-inspector",
        action="store_true",
        help="Expose the lfr-tunnel local inspector dashboard on port 4040",
    )
    imp.add_argument(
        "--share-domain",
        help="Custom domain to use when sharing the instance (e.g. lfr-demo.online, lfr-demo.se)",
    )
    imp.add_argument(
        "--auto-install-lfr-tunnel",
        action="store_true",
        help="Automatically install lfr-tunnel if not found in PATH",
    )
    imp.add_argument("--backup-dir")
    imp.add_argument("--build", action="store_true")
    imp.add_argument("--host-name")
    imp.add_argument("--ssl", action="store_true", default=None)
    imp.add_argument("--no-ssl", action="store_false", dest="ssl")
    imp.add_argument("--port", type=int)
    imp.add_argument("--db", choices=["postgresql", "mysql", "hypersonic"])
    imp.add_argument(
        "--lean", action="store_true", help="Use a resource-optimized JVM profile"
    )
    imp.add_argument("--mount-logs", action="store_true")
    imp.add_argument("--gogo-port", type=int)
    imp.add_argument("--jvm-args", help="Override Liferay JVM arguments")
    imp.add_argument(
        "--tag-latest",
        action="store_true",
        help="Automatically use the latest Liferay tag",
    )
    imp.add_argument("--tag-prefix", help="Prefix for Liferay tag discovery")
    imp.add_argument("--no-vol-cache", action="store_true")
    imp.add_argument("--internal-state", action="store_true")
    imp.add_argument("--no-jvm-verify", action="store_true")
    imp.add_argument("--no-tld-skip", action="store_true")
    imp.add_argument("--no-seed", action="store_true")
    imp.add_argument("--no-osgi-seed", action="store_true")
    imp.add_argument("--sidecar", action="store_true")
    imp.add_argument("--no-captcha", action="store_true")
    imp.add_argument("--fast-login", action="store_true")
    imp.add_argument("--feature", nargs="+")
    imp.add_argument(
        "--verify", action="store_true", default=True, help="Verify snapshot integrity"
    )
    imp.add_argument(
        "--no-verify",
        action="store_false",
        dest="verify",
        help="Skip snapshot integrity verification",
    )
    imp.add_argument(
        "--persist-osgi",
        action="store_true",
        default=None,
        help="Persist the OSGi state folder across container restarts",
    )
    imp.add_argument(
        "--no-persist-osgi",
        action="store_false",
        dest="persist_osgi",
        help="Do not persist the OSGi state folder",
    )
    imp.add_argument("--env", action="append")

    # Command: hydrate
    hydrate = subparsers.add_parser("hydrate", parents=[base_sub_parent])
    hydrate.add_argument("backup_path", help="Path to local cloud backup directory")
    hydrate.add_argument("project", nargs="?")
    hydrate.add_argument("-p", "--project", dest="project_flag")
    hydrate.add_argument("-t", "--tag", help="Liferay Tag (e.g. 2024.q1.3)")
    hydrate.add_argument(
        "--db",
        choices=["postgresql", "mysql"],
        help="Database type for the seed",
    )

    # Command: init
    init = subparsers.add_parser("init", parents=[base_sub_parent])
    init.add_argument("project", nargs="?")
    init.add_argument(
        "-a",
        "--archetype",
        help="Apply an Extensible Stack Archetype (e.g. 'keycloak-sso', 'clustered')",
    )
    init.add_argument("-t", "--tag", help="Liferay Tag (e.g. 2025.q1.0)")
    init.add_argument(
        "--tag-latest",
        action="store_true",
        help="Automatically use the latest Liferay tag",
    )
    init.add_argument("--host-name", help="Virtual Hostname")
    init.add_argument("--db", choices=["postgresql", "mysql", "hypersonic", "external"])
    init.add_argument("--internal-state", action="store_true")
    init.add_argument(
        "--samples", action="store_true", help="Initialize with sample extensions"
    )
    init.add_argument("--sidecar", action="store_true")
    init.add_argument(
        "--expose",
        action="store_true",
        help="Configure an ngrok container to expose Liferay to the public internet",
    )
    init.add_argument(
        "--no-captcha",
        action="store_true",
        help="Disable CAPTCHA for Omni-Admin actions",
    )
    init.add_argument(
        "--fast-login",
        action="store_true",
        help="Bypass typical startup prompts (terms of use, password reset)",
    )
    init.add_argument("--feature", nargs="+")

    # Command: init-from
    init_from = subparsers.add_parser("init-from", parents=[base_sub_parent])
    init_from.add_argument("source")
    init_from.add_argument("project", nargs="?")
    init_from.add_argument("-p", "--project", dest="project_flag")
    init_from.add_argument("--cloud-project", help="Liferay Cloud project ID")
    init_from.add_argument("--target-env", default="local")
    init_from.add_argument(
        "--hydrate-from",
        help="Automatically hydrate data from a Liferay Cloud environment",
    )
    init_from.add_argument(
        "--no-env-sync",
        action="store_true",
        help="Skip syncing environment variables from Liferay Cloud",
    )
    init_from.add_argument("--build", action="store_true")
    init_from.add_argument("--host-name")
    init_from.add_argument("--ssl", action="store_true", default=None)
    init_from.add_argument("--no-ssl", action="store_false", dest="ssl")
    init_from.add_argument("--port", type=int)
    init_from.add_argument("--db", choices=["postgresql", "mysql", "hypersonic"])
    init_from.add_argument("--mount-logs", action="store_true")
    init_from.add_argument("--gogo-port", type=int)
    init_from.add_argument("--jvm-args", help="Override Liferay JVM arguments")
    init_from.add_argument(
        "--tag-latest",
        action="store_true",
        help="Automatically use the latest Liferay tag",
    )
    init_from.add_argument("--tag-prefix", help="Prefix for Liferay tag discovery")
    init_from.add_argument("--no-vol-cache", action="store_true")
    init_from.add_argument("--internal-state", action="store_true")
    init_from.add_argument("--no-jvm-verify", action="store_true")
    init_from.add_argument("--no-tld-skip", action="store_true")
    init_from.add_argument("--no-seed", action="store_true")
    init_from.add_argument("--no-osgi-seed", action="store_true")
    init_from.add_argument("--no-captcha", action="store_true")
    init_from.add_argument("--fast-login", action="store_true")
    init_from.add_argument("--feature", nargs="+")
    init_from.add_argument("--sidecar", action="store_true")
    init_from.add_argument("--env", action="append")
    init_from.add_argument("--delay", type=float, default=2.0)

    # Command: monitor
    monitor = subparsers.add_parser("monitor", parents=[base_sub_parent])
    monitor.add_argument("source", nargs="?")
    monitor.add_argument("-p", "--project")
    monitor.add_argument("--delay", type=float, default=2.0)

    # Command: stop, restart, down (alias: rm), logs, deploy
    for cmd in ["stop", "restart", "down", "logs", "deploy"]:
        aliases = []
        if cmd == "down":
            aliases = ["rm"]
        p = subparsers.add_parser(cmd, aliases=aliases, parents=[base_sub_parent])
        p.add_argument("project", nargs="?")

        if cmd == "deploy":
            p.add_argument(
                "targets",
                nargs="*",
                help="Optional: specific services or files to deploy",
            )
            p.add_argument("--rebuild", action="store_true")
        elif cmd == "logs":
            p.add_argument("service", nargs="*")
        else:
            p.add_argument("service", nargs="?")

        p.add_argument("-p", "--project", dest="project_flag")

        if cmd == "down":
            p.add_argument("-V", "--volumes", action="store_true")
            p.add_argument("-d", "--delete", action="store_true")
            p.add_argument("--infra", action="store_true")
            p.add_argument(
                "--clean-hosts",
                action="store_true",
                help="Remove project entries from hosts file",
            )
        if cmd == "logs":
            p.add_argument("-f", "--follow", action="store_true")
            p.add_argument(
                "-n",
                "--tail",
                type=str,
                default="100",
                help="Number of lines to show from the end of the logs (default: 100)",
            )
            p.add_argument(
                "-t",
                "--timestamps",
                action="store_true",
                help="Show timestamps",
            )
            p.add_argument(
                "--since",
                type=str,
                help="Show logs since timestamp (e.g. 2013-01-02T13:23:37Z) or relative (e.g. 42m for 42 minutes)",
            )
            p.add_argument(
                "--until",
                type=str,
                help="Show logs before a timestamp (e.g. 2013-01-02T13:23:37Z) or relative (e.g. 42m for 42 minutes)",
            )
            p.add_argument(
                "--no-wait",
                action="store_true",
                help="Do not wait for container to be available",
            )
            p.add_argument(
                "--infra",
                action="store_true",
                help="View logs for global infrastructure",
            )
            p.add_argument(
                "-i",
                "--instance",
                type=int,
                metavar="N",
                help="Target a specific scaled replica by index (e.g. --instance 2 targets liferay-2)",
            )
        if cmd in ["stop", "restart", "down", "logs"]:
            p.add_argument(
                "--all", action="store_true", help="Apply to all running projects"
            )

    # Command: reindex
    reindex = subparsers.add_parser("reindex", parents=[base_sub_parent])
    reindex.add_argument("project", nargs="?")
    reindex.add_argument("-p", "--project", dest="project_flag")

    # Command: snapshot, restore
    snap = subparsers.add_parser("snapshot", parents=[base_sub_parent])
    snap.add_argument("project", nargs="?")
    snap.add_argument("-p", "--project", dest="project_flag")
    snap.add_argument("-n", "--name")
    snap.add_argument("--files-only", action="store_true")
    snap.add_argument(
        "--delete",
        help="Delete a snapshot by name or index",
    )
    snap.add_argument(
        "--keep-last",
        type=int,
        help="Keep only the specified number of most recent snapshots",
    )
    snap.add_argument(
        "--older-than",
        type=int,
        help="Delete snapshots older than the specified number of days",
    )
    snap.add_argument(
        "--verify",
        action="store_true",
        default=True,
        help="Generate integrity checksum for snapshot",
    )
    snap.add_argument(
        "--no-verify",
        action="store_false",
        dest="verify",
        help="Skip integrity checksum generation",
    )

    rest = subparsers.add_parser("restore", parents=[base_sub_parent])
    rest.add_argument("project", nargs="?")
    rest.add_argument("-p", "--project", dest="project_flag")
    rest.add_argument("-i", "--index", type=int)
    rest.add_argument("-n", "--name")
    rest.add_argument(
        "--latest", action="store_true", help="Restore the most recent snapshot"
    )
    rest.add_argument("--list", action="store_true", help="List available snapshots")
    rest.add_argument(
        "--up",
        action="store_true",
        help="Automatically start the project after restore",
    )
    rest.add_argument("--backup-dir")
    rest.add_argument(
        "--verify", action="store_true", default=True, help="Verify snapshot integrity"
    )
    rest.add_argument(
        "--no-verify",
        action="store_false",
        dest="verify",
        help="Skip snapshot integrity verification",
    )

    # Simple Lifecycle Commands
    info = subparsers.add_parser("info", parents=[base_sub_parent])
    info.add_argument("project", nargs="?")
    info.add_argument("-p", "--project", dest="project_flag")
    reset = subparsers.add_parser("reset", parents=[base_sub_parent])
    reset.add_argument("project", nargs="?")
    reset.add_argument(
        "target",
        nargs="?",
        default="state",
        help="Target to reset: state, search, db, global-search, or all (default: state)",
    )
    reset.add_argument("-p", "--project", dest="project_flag")

    reseed = subparsers.add_parser("re-seed", parents=[base_sub_parent])
    reseed.add_argument("project", nargs="?")
    reseed.add_argument("-p", "--project", dest="project_flag")
    reseed.add_argument("--no-osgi-seed", action="store_true")
    reseed.add_argument(
        "--up",
        action="store_true",
        help="Automatically start the project after reseeding",
    )

    # Cache management
    cache = subparsers.add_parser(
        "cache", aliases=["clear-cache", "clear-tags"], parents=[base_sub_parent]
    )
    cache.add_argument(
        "target",
        nargs="?",
        default="tags",
        help="Target to clear: tags, seeds, samples, all (default: tags)",
    )

    shell = subparsers.add_parser("shell", parents=[base_sub_parent])
    shell.add_argument("project", nargs="?")
    shell.add_argument("service", nargs="?")
    shell.add_argument("-p", "--project", dest="project_flag")

    gogo = subparsers.add_parser("gogo", parents=[base_sub_parent])
    gogo.add_argument("project", nargs="?")
    gogo.add_argument("-p", "--project", dest="project_flag")

    # Command: browser (alias: open)
    browser = subparsers.add_parser(
        "browser", aliases=["open"], parents=[base_sub_parent]
    )
    browser.add_argument("project", nargs="?")
    browser.add_argument("-p", "--project", dest="project_flag")
    browser.add_argument("-u", "--url")
    browser.add_argument("--remove", action="store_true")
    browser.add_argument("--list", action="store_true")

    scale = subparsers.add_parser("scale", parents=[base_sub_parent])
    scale.add_argument("project", nargs="?")
    scale.add_argument("service_scale", nargs="+")
    scale.add_argument("-p", "--project", dest="project_flag")
    scale.add_argument("--timeout", type=int, default=900)
    scale.add_argument(
        "--no-run",
        action="store_true",
        help="Update the metadata without automatically restarting the stack.",
    )

    # Command: wait
    wait_cmd = subparsers.add_parser(
        "wait",
        parents=[base_sub_parent],
        help="Block execution until a project is fully ready (HTTP 200/302).",
    )
    wait_cmd.add_argument("project", nargs="?")
    wait_cmd.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Maximum time to wait in seconds (default: 900)",
    )

    status = subparsers.add_parser("status", aliases=["ps"], parents=[base_sub_parent])
    status.add_argument("project", nargs="?")
    status.add_argument("--all", action="store_true", help="Show all managed projects")
    subparsers.add_parser("list", aliases=["ls"], parents=[base_sub_parent])

    # Command: dashboard
    dashboard = subparsers.add_parser(
        "dashboard",
        parents=[base_sub_parent],
        help="Launch the visual health dashboard",
    )
    dashboard.add_argument(
        "--port", type=int, default=19000, help="Port to run the dashboard on"
    )
    dashboard.add_argument(
        "--host", default="127.0.0.1", help="Host address to bind to"
    )
    dashboard.add_argument(
        "--background", action="store_true", help="Run dashboard in background"
    )

    # Command: mcp
    subparsers.add_parser(
        "mcp",
        parents=[base_sub_parent],
        help="Starts the Model Context Protocol (MCP) JSON-RPC server",
    )

    # Command: ai
    ai = subparsers.add_parser(
        "ai",
        parents=[base_sub_parent],
        help="Start an interactive troubleshooting session with LDM AI",
    )
    ai.add_argument("query", help="What do you want to ask LDM AI?")

    # Command: quickstart
    quickstart_cmd = subparsers.add_parser(
        "quickstart",
        parents=[base_sub_parent],
        help="Bootstrap and start a predefined accelerator demo stack",
    )
    quickstart_cmd.add_argument(
        "template",
        choices=["aica"],
        help="Predefined accelerator template to bootstrap",
    )
    quickstart_cmd.add_argument(
        "--share",
        action="store_true",
        help="Expose the bootstrap stack dynamically using lfr-tunnel",
    )
    quickstart_cmd.add_argument(
        "--share-subdomain",
        help="Custom subdomain to use when sharing the stack",
    )

    # Command: package
    package_cmd = subparsers.add_parser(
        "package",
        parents=[base_sub_parent],
        help="Package a project snapshot into a portable LDM package (.ldmp)",
    )
    package_cmd.add_argument(
        "project",
        nargs="?",
        help="Name of the project to package (defaults to current directory)",
    )
    package_cmd.add_argument(
        "-o",
        "--output",
        help="Directory path to save the generated package (defaults to current working directory)",
    )
    package_cmd.add_argument(
        "--repo",
        help="GitHub Repository owner/repo identifier (defaults to git origin remote resolution)",
    )
    package_cmd.add_argument(
        "--use-latest",
        action="store_true",
        help="Package the latest existing snapshot instead of creating a fresh snapshot",
    )

    # ==================== NAMESPACES ====================

    # Namespace: infra
    infra = subparsers.add_parser(
        "infra", parents=[base_sub_parent], help="Infrastructure management"
    )
    infra_subparsers = infra.add_subparsers(dest="subcommand")

    infra_setup = infra_subparsers.add_parser("setup", parents=[base_sub_parent])
    infra_setup.add_argument(
        "--search",
        action="store_true",
        help="Also initialize Global Search container",
    )
    infra_setup.add_argument(
        "--es7", action="store_true", help="Use Elasticsearch 7 for global search"
    )

    infra_subparsers.add_parser("down", parents=[base_sub_parent])

    infra_restart = infra_subparsers.add_parser("restart", parents=[base_sub_parent])
    infra_restart.add_argument(
        "--search",
        action="store_true",
        help="Also restart Global Search container",
    )
    infra_restart.add_argument(
        "--es7", action="store_true", help="Use Elasticsearch 7 for global search"
    )

    infra_subparsers.add_parser("init-common", parents=[base_sub_parent])

    renew_ssl = infra_subparsers.add_parser("renew-ssl", parents=[base_sub_parent])
    renew_ssl.add_argument("project", nargs="?")
    renew_ssl.add_argument("-p", "--project", dest="project_flag")
    renew_ssl.add_argument(
        "--all", action="store_true", help="Renew SSL for all projects"
    )

    migrate_search = infra_subparsers.add_parser(
        "migrate-search", parents=[base_sub_parent]
    )
    migrate_search.add_argument("project", nargs="?")
    migrate_search.add_argument("-p", "--project", dest="project_flag")

    # Namespace: share
    share = subparsers.add_parser(
        "share",
        parents=[base_sub_parent],
        help="Share local project runtime publicly via lfr-tunnel",
    )
    share_subparsers = share.add_subparsers(dest="subcommand")

    share_start = share_subparsers.add_parser("start", parents=[base_sub_parent])
    share_start.add_argument("project", nargs="?")
    share_start.add_argument("-p", "--project", dest="project_flag")
    share_start.add_argument(
        "--subdomain",
        help="Custom subdomain prefix (defaults to machine hostname)",
    )
    share_start.add_argument(
        "--ports",
        help="Comma-separated ports to expose (defaults to 8080)",
    )
    share_start.add_argument(
        "--provider",
        choices=["lfr-tunnel", "lfr-tunnel-docker", "ngrok"],
        help="Tunnel provider (defaults to lfr-tunnel)",
    )
    share_start.add_argument(
        "--image",
        help="Custom Docker image to use for the sharing tunnel sidecar (defaults to peterjrichards/lfr-tunnel:latest)",
    )
    share_start.add_argument(
        "--inspector",
        action="store_true",
        help="Expose the lfr-tunnel local inspector dashboard on port 4040",
    )
    share_start.add_argument(
        "--domain",
        help="Custom domain prefix (e.g. lfr-demo.online, lfr-demo.se)",
    )
    share_start.add_argument(
        "--auto-install-lfr-tunnel",
        action="store_true",
        help="Automatically install lfr-tunnel if not found in PATH",
    )
    share_inspector = share_subparsers.add_parser(
        "inspector",
        parents=[base_sub_parent],
        help="Expose the lfr-tunnel local inspector dashboard on port 4040 after the fact",
    )
    share_inspector.add_argument("project", nargs="?")
    share_inspector.add_argument("-p", "--project", dest="project_flag")
    share_inspector.add_argument(
        "--port",
        type=int,
        default=4040,
        help="Local port to expose the inspector on (defaults to 4040)",
    )

    share_status = share_subparsers.add_parser("status", parents=[base_sub_parent])
    share_status.add_argument("project", nargs="?")
    share_status.add_argument("-p", "--project", dest="project_flag")

    share_stop = share_subparsers.add_parser("stop", parents=[base_sub_parent])
    share_stop.add_argument("project", nargs="?")
    share_stop.add_argument("-p", "--project", dest="project_flag")

    # Namespace: cloud
    cloud = subparsers.add_parser(
        "cloud", parents=[base_sub_parent], help="Liferay Cloud integrations"
    )
    cloud_subparsers = cloud.add_subparsers(dest="subcommand")

    cloud_fetch = cloud_subparsers.add_parser("fetch", parents=[base_sub_parent])
    cloud_fetch.add_argument("project", nargs="?")
    cloud_fetch.add_argument("env_id", nargs="?")
    cloud_fetch.add_argument("service", nargs="?")
    cloud_fetch.add_argument("-p", "--project", dest="project_flag")
    cloud_fetch.add_argument("--list-envs", action="store_true")
    cloud_fetch.add_argument("--list-backups", action="store_true")
    cloud_fetch.add_argument("--download", action="store_true")
    cloud_fetch.add_argument("--restore", action="store_true")
    cloud_fetch.add_argument("--sync-env", action="store_true")
    cloud_fetch.add_argument(
        "--no-env-sync",
        action="store_true",
        help="Skip syncing environment variables from Liferay Cloud",
    )
    cloud_fetch.add_argument("--logs", action="store_true")
    cloud_fetch.add_argument("-f", "--follow", action="store_true")

    # Namespace: config
    config_parser = subparsers.add_parser(
        "config",
        parents=[base_sub_parent],
        help="Cascading and global config management",
    )
    config_subparsers = config_parser.add_subparsers(dest="subcommand")

    cfg_get = config_subparsers.add_parser("get", parents=[base_sub_parent])
    cfg_get.add_argument("key", nargs="?")

    cfg_set = config_subparsers.add_parser("set", parents=[base_sub_parent])
    cfg_set.add_argument("key")
    cfg_set.add_argument("value")

    cfg_remove = config_subparsers.add_parser("remove", parents=[base_sub_parent])
    cfg_remove.add_argument("key")

    defaults = config_subparsers.add_parser(
        "defaults",
        parents=[base_sub_parent],
        help="View or modify cascading configuration defaults",
    )
    defaults.add_argument("key", nargs="?", help="The configuration key to set or view")
    defaults.add_argument("value", nargs="?", help="The value to set")
    defaults.add_argument(
        "--global",
        dest="global_level",
        action="store_true",
        help="Apply to the global system level (/etc/ldmrc)",
    )
    defaults.add_argument(
        "--remove", action="store_true", help="Remove the custom default"
    )

    env = config_subparsers.add_parser("env", parents=[base_sub_parent])
    env.add_argument("vars", nargs="*")
    env.add_argument("-p", "--project", dest="project_flag")
    env.add_argument("-s", "--service")
    env.add_argument("--remove", action="store_true")
    env.add_argument("--import", action="store_true", dest="import_env")

    feat = config_subparsers.add_parser("feature", parents=[base_sub_parent])
    feat.add_argument("project", nargs="?")
    feat.add_argument("-p", "--project", dest="project_flag")
    feat.add_argument("--enable", nargs="+", help="Enable one or more feature flags")
    feat.add_argument("--disable", nargs="+", help="Disable one or more feature flags")

    log_level = config_subparsers.add_parser("log-level", parents=[base_sub_parent])
    log_level.add_argument("project", nargs="?")
    log_level.add_argument("-p", "--project", dest="project_flag")
    log_level.add_argument("-b", "--bundle")
    log_level.add_argument("-c", "--category")
    log_level.add_argument(
        "-l", "--level", choices=["DEBUG", "INFO", "WARN", "ERROR", "FATAL", "OFF"]
    )
    log_level.add_argument("--remove", action="store_true")
    log_level.add_argument("--list", action="store_true")

    edit_cmd = config_subparsers.add_parser("edit", parents=[base_sub_parent])
    edit_cmd.add_argument("project", nargs="?")
    edit_cmd.add_argument("-p", "--project", dest="project_flag")
    edit_cmd.add_argument(
        "--target",
        choices=["meta", "properties"],
        default="meta",
        help="Which file to edit (default: meta)",
    )

    # Namespace: system
    system = subparsers.add_parser(
        "system",
        parents=[base_sub_parent],
        help="System utility diagnostics and configurations",
    )
    system_subparsers = system.add_subparsers(dest="subcommand")

    relocate = system_subparsers.add_parser(
        "relocate", help="Safely move LDM/Docker data to an external drive"
    )
    relocate.add_argument(
        "target", help="Target directory on the external drive (e.g. /Volumes/SanDisk)"
    )
    relocate.add_argument(
        "--no-move",
        action="store_true",
        help="Skip moving existing data (just create symlinks)",
    )

    prune = system_subparsers.add_parser(
        "prune",
        parents=[base_sub_parent],
        help="Reclaim disk space by safely removing orphaned containers, search snapshots, dangling Docker volumes, and temporary files.",
    )
    prune.add_argument(
        "--clean-hosts",
        action="store_true",
        help="Remove all LDM-tagged entries from hosts file",
    )
    prune.add_argument(
        "--seeds",
        action="store_true",
        help="Also clear the pre-warmed seed cache",
    )
    prune.add_argument(
        "--samples",
        action="store_true",
        help="Also clear the sample extension cache",
    )
    prune.add_argument(
        "--all",
        action="store_true",
        help="Run all pruning operations without asking (includes seeds, samples, and hosts)",
    )

    doctor = system_subparsers.add_parser(
        "doctor",
        parents=[base_sub_parent],
        help="Run comprehensive health checks on your Docker environment, mounts, connectivity, and disk space.",
    )
    doctor.add_argument("project", nargs="?")
    doctor.add_argument("-p", "--project-id", dest="project_flag")
    doctor.add_argument(
        "--system",
        action="store_true",
        help="Show detailed system diagnostic checks",
    )
    doctor.add_argument(
        "--docker",
        action="store_true",
        help="Show detailed Docker diagnostic checks",
    )
    doctor.add_argument(
        "--project",
        action="store_true",
        help="Show detailed Project diagnostic checks",
    )
    doctor.add_argument(
        "--skip-project",
        action="store_true",
        help="Skip project-specific health checks",
    )
    doctor.add_argument(
        "--slug",
        action="store_true",
        help="Output a machine-readable environment identifier",
    )
    doctor.add_argument(
        "--all", action="store_true", help="Run health checks for all projects"
    )
    doctor.add_argument(
        "--fix-hosts",
        action="store_true",
        help="Automatically fix missing entries in /etc/hosts",
    )
    doctor.add_argument(
        "--bundle",
        action="store_true",
        help="Generate a sanitized debug bundle for troubleshooting",
    )
    doctor.add_argument(
        "--detailed",
        action="store_true",
        help="Show detailed troubleshooting hints and fixes",
    )
    doctor.add_argument(
        "--fix",
        action="store_true",
        help="Automatically apply recommended fixes (e.g., pruning, lifting watermarks)",
    )

    upgrade = system_subparsers.add_parser("upgrade", parents=[base_sub_parent])
    upgrade.add_argument(
        "--check",
        "--status",
        action="store_true",
        dest="check_only",
        help="Check for updates without performing the upgrade",
    )
    upgrade.add_argument(
        "--repair",
        action="store_true",
        help="Re-download the current version to fix integrity issues",
    )
    upgrade.add_argument("--timeout", type=int, default=900)
    upgrade.add_argument(
        "--pre-release",
        "--beta",
        dest="pre_release",
        action="store_true",
        help="Include pre-release (beta) versions during upgrade",
    )

    version_cmd = system_subparsers.add_parser("version", parents=[base_sub_parent])
    version_cmd.add_argument(
        "--bump",
        choices=["major", "minor", "patch", "beta"],
        help="Increment the version logically",
    )
    version_cmd.add_argument(
        "--set", dest="set_version", help="Directly set the version string"
    )
    version_cmd.add_argument(
        "--build-info", help="Inject build metadata into the source"
    )
    version_cmd.add_argument(
        "--check", action="store_true", help="Verify version synchronization"
    )
    version_cmd.add_argument(
        "--print", action="store_true", help="Output current version string only"
    )
    version_cmd.add_argument(
        "--promote",
        action="store_true",
        help="Promote the current beta to a stable release",
    )

    system_subparsers.add_parser("dev-setup", parents=[base_sub_parent])

    fix_hosts_cmd = system_subparsers.add_parser(
        "fix-hosts",
        parents=[base_sub_parent],
        help="Append missing project hostnames/subdomains to the hosts file",
    )
    fix_hosts_cmd.add_argument(
        "host_name",
        nargs="?",
        help="Optional hostname/project to fix",
    )

    completion = system_subparsers.add_parser("completion", parents=[base_sub_parent])
    completion.add_argument(
        "shell", choices=["bash", "zsh", "fish", "powershell"], nargs="?"
    )

    setup_completion = system_subparsers.add_parser(
        "setup-completion", parents=[base_sub_parent]
    )
    setup_completion.add_argument(
        "shell", choices=["bash", "zsh", "fish", "powershell"], nargs="?"
    )

    system_subparsers.add_parser("man", parents=[base_sub_parent])

    roi_cmd = system_subparsers.add_parser(
        "roi",
        parents=[base_sub_parent],
        help="Display cumulative developer time saved by using LDM",
    )
    roi_cmd.add_argument(
        "--reset",
        action="store_true",
        help="Reset cumulative ROI metrics back to zero",
    )

    # Overwrite parse_known_args of the parser to run preprocess_args automatically:
    orig_parse_known_args = parser.parse_known_args

    def parse_known_args_wrapper(args=None, namespace=None):
        if args is None:
            import sys

            preprocessed = preprocess_args(sys.argv[1:])
        else:
            preprocessed = preprocess_args(args)
        return orig_parse_known_args(preprocessed, namespace)

    parser.parse_known_args = parse_known_args_wrapper  # type: ignore[method-assign]

    return parser, subparsers


def main():
    # Suppress watchdog warning on macOS when fsevents is missing (kqueue is a fine fallback)
    warnings.filterwarnings("ignore", message="Failed to import fsevents")

    import sys

    # Restore default SIGPIPE handler to avoid BrokenPipeError traceback when using pipelines
    if sys.platform != "win32":
        import signal

        try:
            signal.signal(signal.SIGPIPE, signal.SIG_DFL)
        except AttributeError:
            pass

    sys.argv = preprocess_args(sys.argv)

    parser, subparsers = get_parser()

    if argcomplete:
        # Recursively attach project completer to all choices under subparsers
        def attach_completers(p):
            for action in p._actions:
                if action.dest in ["project", "project_flag"]:
                    action.completer = project_completer
                if isinstance(action, argparse._SubParsersAction):
                    for sub_p in action.choices.values():
                        attach_completers(sub_p)

        attach_completers(parser)
        argcomplete.autocomplete(parser)

    # Use parse_args (intermixed is not supported by subparsers)
    args = parser.parse_args()

    # Root Safety Guard: Prevent running as sudo for non-upgrade/non-fix-hosts commands
    # This protects the ~/.shiv cache from ownership issues.
    is_safe_command = args.command == "system" and getattr(
        args, "subcommand", None
    ) in ["upgrade", "fix-hosts"]
    if platform.system().lower() != "windows" and not is_safe_command:
        import os

        try:
            if os.geteuid() == 0:
                allow_root_file = SCRIPT_DIR / ".ldm_allow_root"
                allow_root = (
                    os.environ.get("LDM_ALLOW_ROOT", "false").lower() == "true"
                    or os.environ.get("GITHUB_ACTIONS", "false").lower() == "true"
                    or allow_root_file.exists()
                )
                if not allow_root:
                    UI.error("Security Risk: Do not run LDM with 'sudo'.")
                    UI.info(
                        "Running as root causes cache ownership issues in your home directory (~/.shiv).\n"
                        "LDM will prompt for your password only when elevated privileges are needed (e.g. hosts file updates)."
                    )
                    UI.info(
                        f"\nSee troubleshooting: {UI.CYAN}https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#troubleshooting-sudo--root-issues{UI.COLOR_OFF}"
                    )
                    if platform.system().lower() == "linux":
                        UI.info(
                            f"\nIf you are using sudo because of Docker permissions, please run:\n"
                            f"{UI.CYAN}sudo usermod -aG docker $USER{UI.COLOR_OFF} and restart your terminal session."
                        )
                    sys.exit(1)
        except AttributeError:
            # os.geteuid() not available on this platform (though handled by system check)
            pass

    if not args.command:
        parser.print_help()
        return

    namespaces = ["infra", "cloud", "config", "system"]
    if args.command in namespaces and not getattr(args, "subcommand", None):
        sub_parser = subparsers.choices[args.command]
        sub_parser.print_help()
        return

    # Set environment variable LDM_DRY_RUN if dry-run is specified
    if getattr(args, "dry_run", False) is True:
        os.environ["LDM_DRY_RUN"] = "true"

    manager = LiferayManager(args)

    # Disambiguation Heuristic:
    # If the user provides 'ldm logs liferay', argparse puts 'liferay' in project.
    # We detect if the first positional arg is actually a service name.
    if args.command in ["logs", "stop", "restart", "down", "rm", "deploy"]:
        service_id = getattr(args, "project", None)
        # Standard Liferay services
        known_services = ["liferay", "db", "search", "proxy", "elasticsearch"]
        if service_id in known_services:
            # Shift arguments: service_id is definitely a service
            if args.command == "logs":
                # logs takes nargs='*'
                args.service = [service_id] + (getattr(args, "service", []) or [])
            else:
                # others take nargs='?'
                args.service = service_id
            args.project = None

    # Use a simpler project name detection for the docker_required check
    # to avoid failing if the detected root doesn't have a name yet (initialization case)
    docker_required = [
        ("run", None),
        ("up", None),
        ("init-from", None),
        ("monitor", None),
        ("stop", None),
        ("restart", None),
        ("down", None),
        ("rm", None),
        ("logs", None),
        ("deploy", None),
        ("snapshot", None),
        ("restore", None),
        ("hydrate", None),
        ("import", None),
        ("scale", None),
        ("status", None),
        ("ps", None),
        ("list", None),
        ("ls", None),
        # Namespaced commands requiring docker:
        ("infra", "down"),
        ("infra", "restart"),
        ("infra", "setup"),
        ("cloud", "fetch"),
        ("config", "env"),
        ("config", "log-level"),
        ("system", "doctor"),
        ("system", "prune"),
    ]
    cmd = getattr(args, "command", None)
    if cmd is not None and not isinstance(cmd, str):
        cmd = None
    subcommand = getattr(args, "subcommand", None)
    if subcommand is not None and not isinstance(subcommand, str):
        subcommand = None
    current_cmd = (cmd, subcommand)
    if current_cmd in docker_required and not manager.check_docker():
        UI.die("Docker not accessible.")

    # Execution map
    from collections.abc import Callable
    from typing import Any

    cmds: dict[tuple[str, str | None], Callable[..., Any]] = {
        ("run", None): lambda: (
            manager.runtime.cmd_run(getattr(args, "project", None)),
            manager.runtime.cmd_browser(getattr(args, "project", None))
            if getattr(args, "open", False)
            else None,
        )[0],
        ("up", None): lambda: (
            manager.runtime.cmd_run(getattr(args, "project", None)),
            manager.runtime.cmd_browser(getattr(args, "project", None))
            if getattr(args, "open", False)
            else None,
        )[0],
        ("dashboard", None): lambda: manager.cmd_dashboard(
            port=getattr(args, "port", 19000),
            host=getattr(args, "host", "127.0.0.1"),
            background=getattr(args, "background", False),
        ),
        ("hydrate", None): lambda: manager.cmd_hydrate(
            args.backup_path, getattr(args, "project", None)
        ),
        ("mcp", None): manager.cmd_mcp,
        ("ai", None): lambda: manager.cmd_ai(args.query),
        ("quickstart", None): lambda: manager.cmd_quickstart(
            args.template,
            share=args.share,
            share_subdomain=args.share_subdomain,
        ),
        ("package", None): lambda: manager.snapshot.cmd_package(
            getattr(args, "project", None),
            output_dir=getattr(args, "output", None),
            repo=getattr(args, "repo", None),
            use_latest=getattr(args, "use_latest", False),
        ),
        ("import", None): lambda: manager.workspace.cmd_import(args.source),
        ("init-from", None): lambda: manager.workspace.cmd_init_from(args.source),
        ("monitor", None): lambda: manager.workspace.cmd_monitor(args.source),
        ("init", None): lambda: manager.workspace.cmd_init(
            getattr(args, "project", None)
        ),
        ("stop", None): lambda: manager.runtime.cmd_stop(
            getattr(args, "project", None),
            getattr(args, "service", None),
            all_projects=args.all,
        ),
        ("restart", None): lambda: manager.runtime.cmd_restart(
            getattr(args, "project", None),
            getattr(args, "service", None),
            all_projects=args.all,
        ),
        ("down", None): lambda: manager.runtime.cmd_down(
            getattr(args, "project", None),
            getattr(args, "service", None),
            all_projects=args.all,
            delete=getattr(args, "delete", False),
            infra=getattr(args, "infra", False),
            clean_hosts=getattr(args, "clean_hosts", False),
        ),
        ("rm", None): lambda: manager.runtime.cmd_down(
            getattr(args, "project", None),
            getattr(args, "service", None),
            all_projects=args.all,
            delete=getattr(args, "delete", False),
            infra=getattr(args, "infra", False),
            clean_hosts=getattr(args, "clean_hosts", False),
        ),
        ("logs", None): lambda: manager.runtime.cmd_logs(
            getattr(args, "project", None),
            getattr(args, "service", None),
            all_projects=args.all,
            infra=getattr(args, "infra", False),
            follow=getattr(args, "follow", False),
            no_wait=getattr(args, "no_wait", False),
            tail=getattr(args, "tail", "100"),
            timestamps=getattr(args, "timestamps", False),
            since=getattr(args, "since", None),
            until=getattr(args, "until", None),
            instance=getattr(args, "instance", None),
        ),
        ("deploy", None): lambda: manager.cmd_deploy(
            getattr(args, "project", None), targets=getattr(args, "targets", [])
        ),
        ("reindex", None): lambda: manager.runtime.cmd_reindex(
            getattr(args, "project", None)
        ),
        ("snapshot", None): lambda: manager.snapshot.cmd_snapshot(
            getattr(args, "project", None)
        ),
        ("restore", None): lambda: manager.snapshot.cmd_restore(
            getattr(args, "project", None)
        ),
        ("info", None): lambda: manager.diagnostics.cmd_info(
            getattr(args, "project", None)
        ),
        ("reset", None): lambda: manager.runtime.cmd_reset(
            getattr(args, "project", None), getattr(args, "target", "state")
        ),
        ("re-seed", None): lambda: manager.runtime.cmd_reseed(
            getattr(args, "project", None)
        ),
        ("cache", None): lambda: manager.diagnostics.cmd_cache(
            getattr(args, "target", "tags")
        ),
        ("clear-cache", None): lambda: manager.diagnostics.cmd_cache("tags"),
        ("clear-tags", None): lambda: manager.diagnostics.cmd_cache("tags"),
        ("wait", None): lambda: manager.cmd_wait(
            getattr(args, "project", None), timeout=getattr(args, "timeout", 600)
        ),
        ("status", None): lambda: manager.diagnostics.cmd_status(
            getattr(args, "project", None), all_projects=args.all
        ),
        ("ps", None): lambda: manager.diagnostics.cmd_status(
            getattr(args, "project", None), all_projects=args.all
        ),
        ("list", None): manager.diagnostics.cmd_list,
        ("ls", None): manager.diagnostics.cmd_list,
        ("shell", None): lambda: manager.runtime.cmd_shell(
            getattr(args, "project", None), getattr(args, "service", None)
        ),
        ("gogo", None): lambda: manager.runtime.cmd_gogo(
            getattr(args, "project", None)
        ),
        ("browser", None): lambda: manager.runtime.cmd_browser(
            getattr(args, "project", None)
        ),
        ("open", None): lambda: manager.runtime.cmd_browser(
            getattr(args, "project", None)
        ),
        ("scale", None): lambda: manager.runtime.cmd_scale(
            getattr(args, "project", None),
            args.service_scale,
            getattr(args, "no_run", False),
        ),
        # share namespace:
        ("share", "start"): lambda: manager.share.cmd_start(
            project_id=getattr(args, "project", None)
            or getattr(args, "project_flag", None),
            subdomain=getattr(args, "subdomain", None),
            ports=getattr(args, "ports", None),
            provider=getattr(args, "provider", None),
            image=getattr(args, "image", None),
            inspector=getattr(args, "inspector", False),
        ),
        ("share", "inspector"): lambda: manager.share.cmd_inspector(
            project_id=getattr(args, "project", None)
            or getattr(args, "project_flag", None),
            port=getattr(args, "port", 4040),
        ),
        ("share", "status"): lambda: manager.share.cmd_status(
            project_id=getattr(args, "project", None)
            or getattr(args, "project_flag", None),
        ),
        ("share", "stop"): lambda: manager.share.cmd_stop(
            project_id=getattr(args, "project", None)
            or getattr(args, "project_flag", None),
        ),
        # infra namespace:
        ("infra", "setup"): manager.infra.cmd_infra_setup,
        ("infra", "down"): manager.infra.cmd_infra_down,
        ("infra", "restart"): manager.infra.cmd_infra_restart,
        ("infra", "init-common"): manager.config.cmd_init_common,
        ("infra", "renew-ssl"): lambda: manager.runtime.cmd_renew_ssl(
            getattr(args, "project", None)
        ),
        ("infra", "migrate-search"): lambda: manager.runtime.cmd_migrate_search(
            getattr(args, "project", None)
        ),
        # cloud namespace:
        ("cloud", "fetch"): lambda: manager.cloud.cmd_cloud_fetch(
            getattr(args, "project", None),
            getattr(args, "env_id", None),
            follow=getattr(args, "follow", False),
        ),
        # config namespace:
        ("config", "get"): lambda: manager.config.cmd_config(
            getattr(args, "key", None), None
        ),
        ("config", "set"): lambda: manager.config.cmd_config(args.key, args.value),
        ("config", "remove"): lambda: (
            setattr(args, "remove", True)  # type: ignore[func-returns-value]
            or manager.config.cmd_config(args.key, "unset")
        ),
        ("config", "defaults"): lambda: manager.config.cmd_defaults(
            getattr(args, "key", None), getattr(args, "value", None)
        ),
        ("config", "env"): lambda: manager.config.cmd_env(
            getattr(args, "project", None)
        ),
        ("config", "feature"): lambda: manager.config.cmd_feature(
            getattr(args, "project", None),
            enable=getattr(args, "enable", None),
            disable=getattr(args, "disable", None),
        ),
        ("config", "log-level"): lambda: manager.config.cmd_log_level(
            getattr(args, "project", None)
        ),
        ("config", "edit"): lambda: manager.config.cmd_edit(
            getattr(args, "project", None), args.target
        ),
        # system namespace:
        ("system", "relocate"): lambda: manager.infra.cmd_system("relocate"),
        ("system", "prune"): manager.diagnostics.cmd_prune,
        ("system", "doctor"): lambda: manager.cmd_doctor(
            getattr(args, "project", None), all_projects=args.all
        ),
        ("system", "upgrade"): manager.diagnostics.cmd_upgrade,
        ("system", "version"): lambda: manager.dev.cmd_version(
            bump_type=args.bump,
            promote=args.promote,
            set_version=args.set_version,
            build_info=args.build_info,
            check=args.check,
            print_only=args.print,
        ),
        ("system", "dev-setup"): manager.dev.cmd_dev_setup,
        ("system", "completion"): lambda: manager.cmd_completion(args.shell),
        ("system", "setup-completion"): lambda: manager.cmd_setup_completion(
            args.shell
        ),
        ("system", "man"): manager.cmd_man,
        ("system", "fix-hosts"): lambda: manager.cmd_fix_hosts(
            getattr(args, "host_name", None)
        ),
        ("system", "roi"): manager.config.cmd_roi,
    }

    if current_cmd in cmds:
        import threading

        update_info = {}

        def run_update_check():
            latest: Any = None
            latest, _url = check_for_updates(VERSION)
            update_info["latest"] = latest

        update_thread = None
        is_upgrade_or_completion = args.command == "system" and getattr(
            args, "subcommand", None
        ) in ["upgrade", "completion", "setup-completion"]
        if not is_upgrade_or_completion:
            update_thread = threading.Thread(target=run_update_check, daemon=True)
            update_thread.start()

        try:
            from ldm_core.utils import Benchmarker

            if getattr(args, "benchmark", False):
                Benchmarker.start()

            cmds[current_cmd]()

            if getattr(args, "benchmark", False):
                Benchmarker.print_report()

        except KeyboardInterrupt:
            print(f"\n{UI.WHITE}Aborted.{UI.COLOR_OFF}")
            sys.exit(130)
        except Exception as e:
            UI.die("An unexpected error occurred.", details=e)

        is_completion = args.command == "system" and getattr(
            args, "subcommand", None
        ) in ["completion", "setup-completion"]
        if update_thread and not is_completion:
            update_thread.join(timeout=0.05)
            latest = update_info.get("latest")
            if latest:
                from ldm_core.utils import version_to_tuple

                if version_to_tuple(latest) > version_to_tuple(VERSION):
                    print(
                        f"\n{UI.BYELLOW}[!] A new version of LDM is available: v{latest}{UI.COLOR_OFF}"
                    )
                    print(
                        f"    Run {UI.CYAN}ldm system upgrade{UI.COLOR_OFF} to install the latest version.\n"
                    )
    else:
        parser.print_help()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        # Graceful exit for CTRL+C or CTRL+D
        import sys

        sys.stdout.write("\n\r")
        sys.exit(0)
