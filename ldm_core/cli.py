import argparse
import sys
import warnings
import platform
from ldm_core.ui import UI
from ldm_core.manager import LiferayManager
from ldm_core.constants import VERSION, SCRIPT_DIR
from ldm_core.utils import check_for_updates

try:
    import argcomplete
except ImportError:
    argcomplete = None


def project_completer(prefix, **kwargs):
    from ldm_core.utils import find_dxp_roots

    roots = find_dxp_roots()
    return [r["path"].name for r in roots if r["path"].name.startswith(prefix)]


def get_parser():
    # Define a parent parser for common arguments shared by all subparsers
    # This allows flags like -v and -y to be placed both before AND after subcommands
    base_parent = argparse.ArgumentParser(add_help=False)
    base_parent.add_argument("-v", "--verbose", action="store_true")
    base_parent.add_argument("-y", "--non-interactive", action="store_true")

    # For subparsers, we want the global flags but we SUPPRESS the default (False)
    # so they don't overwrite the value set by the main parser if provided before the command.
    base_sub_parent = argparse.ArgumentParser(
        add_help=False, argument_default=argparse.SUPPRESS
    )
    base_sub_parent.add_argument("-v", "--verbose", action="store_true")
    base_sub_parent.add_argument("-y", "--non-interactive", action="store_true")

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
    run.add_argument("--sidecar", action="store_true")
    run.add_argument(
        "--es7", action="store_true", help="Use Elasticsearch 7 for global search"
    )
    run.add_argument("--no-up", action="store_true")
    run.add_argument("--no-wait", action="store_true")
    run.add_argument("--mount-logs", action="store_true")
    run.add_argument("--gogo-port", type=int)
    run.add_argument("--jvm-args", help="Override Liferay JVM arguments")
    run.add_argument(
        "--no-vol-cache",
        action="store_true",
        help="Disable :cached volumes on macOS/Windows",
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

    # Command: import
    imp = subparsers.add_parser("import", parents=[base_sub_parent])
    imp.add_argument("source")
    imp.add_argument("project", nargs="?")
    imp.add_argument("-p", "--project", dest="project_flag")
    imp.add_argument("--target-env", default="local")
    imp.add_argument("--no-run", action="store_true")
    imp.add_argument("--backup-dir")
    imp.add_argument("--build", action="store_true")
    imp.add_argument("--host-name")
    imp.add_argument("--ssl", action="store_true", default=None)
    imp.add_argument("--no-ssl", action="store_false", dest="ssl")
    imp.add_argument("--port", type=int)
    imp.add_argument("--db", choices=["postgresql", "mysql", "hypersonic"])
    imp.add_argument("--mount-logs", action="store_true")
    imp.add_argument("--gogo-port", type=int)
    imp.add_argument("--jvm-args", help="Override Liferay JVM arguments")
    imp.add_argument("--tag-prefix", help="Prefix for Liferay tag discovery")
    imp.add_argument("--no-vol-cache", action="store_true")
    imp.add_argument("--no-jvm-verify", action="store_true")
    imp.add_argument("--no-tld-skip", action="store_true")
    imp.add_argument("--no-seed", action="store_true")
    imp.add_argument("--no-osgi-seed", action="store_true")
    imp.add_argument("--env", action="append")

    # Command: init-from
    init_from = subparsers.add_parser("init-from", parents=[base_sub_parent])
    init_from.add_argument("source")
    init_from.add_argument("project", nargs="?")
    init_from.add_argument("-p", "--project", dest="project_flag")
    init_from.add_argument("--target-env", default="local")
    init_from.add_argument("--build", action="store_true")
    init_from.add_argument("--host-name")
    init_from.add_argument("--ssl", action="store_true", default=None)
    init_from.add_argument("--no-ssl", action="store_false", dest="ssl")
    init_from.add_argument("--port", type=int)
    init_from.add_argument("--db", choices=["postgresql", "mysql", "hypersonic"])
    init_from.add_argument("--mount-logs", action="store_true")
    init_from.add_argument("--gogo-port", type=int)
    init_from.add_argument("--jvm-args", help="Override Liferay JVM arguments")
    init_from.add_argument("--tag-prefix", help="Prefix for Liferay tag discovery")
    init_from.add_argument("--no-vol-cache", action="store_true")
    init_from.add_argument("--no-jvm-verify", action="store_true")
    init_from.add_argument("--no-tld-skip", action="store_true")
    init_from.add_argument("--no-seed", action="store_true")
    init_from.add_argument("--no-osgi-seed", action="store_true")
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

        if cmd == "logs":
            p.add_argument("service", nargs="*")
        else:
            p.add_argument("service", nargs="?")

        p.add_argument("-p", "--project", dest="project_flag")
        if cmd == "down":
            p.add_argument("-V", "--volumes", action="store_true")
            p.add_argument("-d", "--delete", action="store_true")
            p.add_argument("--infra", action="store_true")
        if cmd == "logs":
            p.add_argument("-f", "--follow", action="store_true")
            p.add_argument(
                "--tail",
                type=str,
                default="100",
                help="Number of lines to show from the end of the logs (default: 100)",
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
        if cmd == "deploy":
            p.add_argument("--rebuild", action="store_true")
        if cmd in ["stop", "restart", "down", "logs"]:
            p.add_argument(
                "--all", action="store_true", help="Apply to all running projects"
            )

    # Command: env
    env = subparsers.add_parser("env", parents=[base_sub_parent])
    env.add_argument("vars", nargs="*")
    env.add_argument("-p", "--project", dest="project_flag")
    env.add_argument("-s", "--service")
    env.add_argument("--remove", action="store_true")
    env.add_argument("--import", action="store_true", dest="import_env")

    # Command: snapshot, restore
    snap = subparsers.add_parser("snapshot", parents=[base_sub_parent])
    snap.add_argument("project", nargs="?")
    snap.add_argument("-p", "--project", dest="project_flag")
    snap.add_argument("-n", "--name")
    snap.add_argument("--files-only", action="store_true")

    rest = subparsers.add_parser("restore", parents=[base_sub_parent])
    rest.add_argument("project", nargs="?")
    rest.add_argument("-p", "--project", dest="project_flag")
    rest.add_argument("-i", "--index", type=int)
    rest.add_argument("--list", action="store_true", help="List available snapshots")
    rest.add_argument("--backup-dir")

    # Simple Commands
    subparsers.add_parser("init-common", parents=[base_sub_parent])
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

    renew_ssl = subparsers.add_parser("renew-ssl", parents=[base_sub_parent])
    renew_ssl.add_argument("project", nargs="?")
    renew_ssl.add_argument("-p", "--project", dest="project_flag")
    renew_ssl.add_argument(
        "--all", action="store_true", help="Renew SSL for all projects"
    )

    infra_setup = subparsers.add_parser("infra-setup", parents=[base_sub_parent])
    infra_setup.add_argument(
        "--search",
        action="store_true",
        help="Also initialize Global Search container",
    )
    infra_setup.add_argument(
        "--es7", action="store_true", help="Use Elasticsearch 7 for global search"
    )
    subparsers.add_parser("infra-down", parents=[base_sub_parent])
    subparsers.add_parser("infra-restart", parents=[base_sub_parent])

    # Cache management
    cache = subparsers.add_parser(
        "cache", aliases=["clear-cache", "clear-tags"], parents=[base_sub_parent]
    )
    cache.add_argument(
        "target",
        nargs="?",
        default="tags",
        help="Target to clear: tags, seeds, all (default: tags)",
    )

    upgrade = subparsers.add_parser("upgrade", parents=[base_sub_parent])
    upgrade.add_argument(
        "--repair",
        action="store_true",
        help="Re-download the current version to fix integrity issues",
    )
    subparsers.add_parser("update-check", parents=[base_sub_parent])
    migrate_search = subparsers.add_parser("migrate-search", parents=[base_sub_parent])
    migrate_search.add_argument("project", nargs="?")
    migrate_search.add_argument("-p", "--project", dest="project_flag")

    doctor = subparsers.add_parser("doctor", parents=[base_sub_parent])
    doctor.add_argument("project", nargs="?")
    doctor.add_argument("-p", "--project", dest="project_flag")
    doctor.add_argument(
        "--skip-project",
        action="store_true",
        help="Skip project-specific health checks",
    )
    doctor.add_argument(
        "--all", action="store_true", help="Run health checks for all projects"
    )
    doctor.add_argument(
        "--fix-hosts",
        action="store_true",
        help="Automatically fix missing entries in /etc/hosts",
    )

    status = subparsers.add_parser("status", aliases=["ps"], parents=[base_sub_parent])
    status.add_argument("project", nargs="?")
    status.add_argument("--all", action="store_true", help="Show all managed projects")
    subparsers.add_parser("list", aliases=["ls"], parents=[base_sub_parent])

    # Command: config
    config = subparsers.add_parser("config", parents=[base_sub_parent])
    config.add_argument("key", nargs="?")
    config.add_argument("value", nargs="?")
    config.add_argument("--remove", action="store_true")
    subparsers.add_parser("prune", parents=[base_sub_parent])

    shell = subparsers.add_parser("shell", parents=[base_sub_parent])
    shell.add_argument("project", nargs="?")
    shell.add_argument("service", nargs="?")
    shell.add_argument("-p", "--project", dest="project_flag")

    gogo = subparsers.add_parser("gogo", parents=[base_sub_parent])
    gogo.add_argument("project", nargs="?")
    gogo.add_argument("-p", "--project", dest="project_flag")

    log_level = subparsers.add_parser("log-level", parents=[base_sub_parent])
    log_level.add_argument("project", nargs="?")
    log_level.add_argument("-p", "--project", dest="project_flag")
    log_level.add_argument("-b", "--bundle")
    log_level.add_argument("-c", "--category")
    log_level.add_argument(
        "-l", "--level", choices=["DEBUG", "INFO", "WARN", "ERROR", "FATAL", "OFF"]
    )
    log_level.add_argument("--remove", action="store_true")
    log_level.add_argument("--list", action="store_true")

    # Command: browser (alias: open)
    browser = subparsers.add_parser(
        "browser", aliases=["open"], parents=[base_sub_parent]
    )
    browser.add_argument("project", nargs="?")
    browser.add_argument("-p", "--project", dest="project_flag")
    browser.add_argument("-u", "--url")
    browser.add_argument("--remove", action="store_true")
    browser.add_argument("--list", action="store_true")

    # Command: edit
    edit_cmd = subparsers.add_parser("edit", parents=[base_sub_parent])
    edit_cmd.add_argument("project", nargs="?")
    edit_cmd.add_argument("-p", "--project", dest="project_flag")
    edit_cmd.add_argument(
        "--target",
        choices=["meta", "properties"],
        default="meta",
        help="Which file to edit (default: meta)",
    )

    # Command: completion
    completion = subparsers.add_parser("completion", parents=[base_sub_parent])
    completion.add_argument(
        "shell", choices=["bash", "zsh", "fish", "powershell"], nargs="?"
    )

    # Command: man
    subparsers.add_parser("man", parents=[base_sub_parent])

    scale = subparsers.add_parser("scale", parents=[base_sub_parent])
    scale.add_argument("project", nargs="?")
    scale.add_argument("service_scale", nargs="+")
    scale.add_argument("-p", "--project", dest="project_flag")

    cloud = subparsers.add_parser("cloud-fetch", parents=[base_sub_parent])
    cloud.add_argument("project", nargs="?")
    cloud.add_argument("env_id", nargs="?")
    cloud.add_argument("service", nargs="?")
    cloud.add_argument("-p", "--project", dest="project_flag")
    cloud.add_argument("--list-envs", action="store_true")
    cloud.add_argument("--list-backups", action="store_true")
    cloud.add_argument("--download", action="store_true")
    cloud.add_argument("--restore", action="store_true")
    cloud.add_argument("--sync-env", action="store_true")
    cloud.add_argument("--logs", action="store_true")
    cloud.add_argument("-f", "--follow", action="store_true")

    return parser, subparsers


def main():
    # Suppress watchdog warning on macOS when fsevents is missing (kqueue is a fine fallback)
    warnings.filterwarnings("ignore", message="Failed to import fsevents")

    parser, subparsers = get_parser()

    if argcomplete:
        # Automatically attach the project completer to all project-related arguments
        # across all subparsers.
        for sub in subparsers.choices.values():
            for action in sub._actions:
                if action.dest in ["project", "project_flag"]:
                    action.completer = project_completer
        argcomplete.autocomplete(parser)

    # Use parse_args (intermixed is not supported by subparsers)
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    # Root Safety Guard: Prevent running as sudo for non-upgrade commands
    # This protects the ~/.shiv cache from ownership issues.
    if platform.system().lower() != "windows":
        import os

        if os.geteuid() == 0:
            allow_root_file = SCRIPT_DIR / ".ldm_allow_root"
            allow_root = (
                os.environ.get("LDM_ALLOW_ROOT", "false").lower() == "true"
                or allow_root_file.exists()
            )
            if not allow_root:
                UI.error("Security Risk: Do not run LDM with 'sudo'.")
                UI.info(
                    "Running as root causes cache ownership issues in your home directory (~/.shiv).\n"
                    "LDM will prompt for your password only when elevated privileges are needed (e.g. hosts file updates)."
                )
                UI.info(
                    f"\nSee troubleshooting: {UI.CYAN}https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#troubleshooting-sudo--root-issues{UI.COLOR_OFF}"
                )
                if platform.system().lower() == "linux":
                    UI.info(
                        f"\nIf you are using sudo because of Docker permissions, please run:\n"
                        f"{UI.CYAN}sudo usermod -aG docker $USER{UI.COLOR_OFF} and restart your terminal session."
                    )
                sys.exit(1)

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
        "run",
        "init-from",
        "monitor",
        "stop",
        "restart",
        "down",
        "infra-down",
        "logs",
        "deploy",
        "env",
        "snapshot",
        "restore",
        "import",
        "scale",
        "log-level",
        "cloud-fetch",
        "status",
        "ps",
        "list",
        "ls",
    ]
    if args.command in docker_required and not manager.check_docker():
        UI.die("Docker not accessible.")

    # Execution map
    cmds = {
        "run": lambda: manager.cmd_run(getattr(args, "project", None)),
        "up": lambda: manager.cmd_run(getattr(args, "project", None)),
        "import": lambda: manager.cmd_import(args.source),
        "init-from": lambda: manager.cmd_init_from(args.source),
        "monitor": lambda: manager.cmd_monitor(args.source),
        "stop": lambda: manager.cmd_stop(
            getattr(args, "project", None),
            getattr(args, "service", None),
            all_projects=args.all,
        ),
        "restart": lambda: manager.cmd_restart(
            getattr(args, "project", None),
            getattr(args, "service", None),
            all_projects=args.all,
        ),
        "down": lambda: manager.cmd_down(
            getattr(args, "project", None),
            getattr(args, "service", None),
            all_projects=args.all,
            delete=getattr(args, "delete", False),
            infra=getattr(args, "infra", False),
        ),
        "rm": lambda: manager.cmd_down(
            getattr(args, "project", None),
            getattr(args, "service", None),
            all_projects=args.all,
            delete=getattr(args, "delete", False),
            infra=getattr(args, "infra", False),
        ),
        "logs": lambda: manager.cmd_logs(
            getattr(args, "project", None),
            getattr(args, "service", None),
            all_projects=args.all,
            infra=getattr(args, "infra", False),
            follow=getattr(args, "follow", False),
            no_wait=getattr(args, "no_wait", False),
            tail=getattr(args, "tail", "100"),
        ),
        "deploy": lambda: manager.cmd_deploy(
            getattr(args, "project", None), getattr(args, "service", None)
        ),
        "env": lambda: manager.cmd_env(getattr(args, "project", None)),
        "snapshot": lambda: manager.cmd_snapshot(getattr(args, "project", None)),
        "restore": lambda: manager.cmd_restore(getattr(args, "project", None)),
        "init-common": lambda: manager.cmd_init_common(),
        "reset": lambda: manager.cmd_reset(
            getattr(args, "project", None), getattr(args, "target", "state")
        ),
        "re-seed": lambda: manager.cmd_reseed(getattr(args, "project", None)),
        "migrate-search": lambda: manager.cmd_migrate_search(
            getattr(args, "project", None)
        ),
        "renew-ssl": lambda: manager.cmd_renew_ssl(getattr(args, "project", None)),
        "infra-setup": lambda: manager.cmd_infra_setup(),
        "infra-down": lambda: manager.cmd_infra_down(),
        "infra-restart": lambda: manager.cmd_infra_restart(),
        "cache": lambda: manager.cmd_cache(getattr(args, "target", "tags")),
        "clear-cache": lambda: manager.cmd_cache("tags"),
        "clear-tags": lambda: manager.cmd_cache("tags"),
        "doctor": lambda: manager.cmd_doctor(
            getattr(args, "project", None), all_projects=args.all
        ),
        "status": lambda: manager.cmd_status(
            getattr(args, "project", None), all_projects=args.all
        ),
        "ps": lambda: manager.cmd_status(
            getattr(args, "project", None), all_projects=args.all
        ),
        "list": lambda: manager.cmd_list(),
        "ls": lambda: manager.cmd_list(),
        "config": lambda: manager.cmd_config(args.key, args.value),
        "shell": lambda: manager.cmd_shell(
            getattr(args, "project", None), getattr(args, "service", None)
        ),
        "gogo": lambda: manager.cmd_gogo(getattr(args, "project", None)),
        "log-level": lambda: manager.cmd_log_level(getattr(args, "project", None)),
        "browser": lambda: manager.cmd_browser(getattr(args, "project", None)),
        "open": lambda: manager.cmd_browser(getattr(args, "project", None)),
        "scale": lambda: manager.cmd_scale(
            getattr(args, "project", None), args.service_scale
        ),
        "cloud-fetch": lambda: manager.cmd_cloud_fetch(
            getattr(args, "project", None),
            getattr(args, "env_id", None),
            follow=getattr(args, "follow", False),
        ),
        "edit": lambda: manager.cmd_edit(getattr(args, "project", None), args.target),
        "completion": lambda: manager.cmd_completion(args.shell),
        "man": lambda: manager.cmd_man(),
        "prune": lambda: manager.cmd_prune(),
        "upgrade": lambda: manager.cmd_upgrade(),
        "update-check": lambda: manager.cmd_update_check(force=True),
    }

    if args.command in cmds:
        import threading

        update_info = {}

        def run_update_check():
            latest, url = check_for_updates(VERSION)
            update_info["latest"] = latest

        update_thread = None
        if args.command not in ["upgrade", "update-check", "completion"]:
            update_thread = threading.Thread(target=run_update_check, daemon=True)
            update_thread.start()

        try:
            cmds[args.command]()
        except KeyboardInterrupt:
            print(f"\n{UI.WHITE}Aborted.{UI.COLOR_OFF}")
            sys.exit(130)
        except Exception as e:
            UI.error("An unexpected error occurred.", e)
            if "-v" in sys.argv or "--verbose" in sys.argv:
                import traceback

                traceback.print_exc()
            sys.exit(1)

        if update_thread and args.command != "completion":
            update_thread.join(timeout=0.05)
            latest = update_info.get("latest")
            if latest:
                from ldm_core.utils import version_to_tuple

                if version_to_tuple(latest) > version_to_tuple(VERSION):
                    print(
                        f"\n{UI.BYELLOW}[!] A new version of LDM is available: v{latest}{UI.COLOR_OFF}"
                    )
                    print(
                        f"    Run {UI.CYAN}ldm upgrade{UI.COLOR_OFF} to install the latest version.\n"
                    )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
