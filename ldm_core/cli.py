import argparse
from ldm_core.ui import UI
from ldm_core.manager import LiferayManager


def main():
    parser = argparse.ArgumentParser(description="Liferay Docker Manager (ldm)")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--select", action="store_true")
    parser.add_argument("-y", "--non-interactive", action="store_true")
    subparsers = parser.add_subparsers(dest="command")

    # Command: run
    run = subparsers.add_parser("run")
    run.add_argument("project", nargs="?")
    run.add_argument("-t", "--tag")
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
    run.add_argument("--no-up", action="store_true")
    run.add_argument("--no-wait", action="store_true")
    run.add_argument("--mount-logs", action="store_true")
    run.add_argument("--gogo-port", type=int)
    run.add_argument("-f", "--follow", action="store_true")
    run.add_argument("--env", action="append")

    # Command: import
    imp = subparsers.add_parser("import", aliases=["init-from"])
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
    imp.add_argument("--env", action="append")

    # Command: monitor
    monitor = subparsers.add_parser("monitor")
    monitor.add_argument("source", nargs="?")
    monitor.add_argument("-p", "--project")
    monitor.add_argument("--delay", type=float, default=2.0)

    # Command: stop, restart, down, logs, deploy
    for cmd in ["stop", "restart", "down", "logs", "deploy"]:
        p = subparsers.add_parser(cmd)
        p.add_argument("project", nargs="?")
        p.add_argument("service", nargs="?")
        p.add_argument("-p", "--project", dest="project_flag")
        if cmd == "down":
            p.add_argument("-v", "--volumes", action="store_true")
            p.add_argument("-d", "--delete", action="store_true")
            p.add_argument("--infra", action="store_true")
        if cmd == "deploy":
            p.add_argument("--rebuild", action="store_true")

    # Command: env
    env = subparsers.add_parser("env")
    env.add_argument("vars", nargs="*")
    env.add_argument("-p", "--project", dest="project_flag")
    env.add_argument("-s", "--service")
    env.add_argument("--remove", action="store_true")
    env.add_argument("--import", action="store_true", dest="import_env")

    # Command: snapshot, restore
    snap = subparsers.add_parser("snapshot")
    snap.add_argument("project", nargs="?")
    snap.add_argument("-p", "--project", dest="project_flag")
    snap.add_argument("-n", "--name")
    snap.add_argument("--files-only", action="store_true")

    rest = subparsers.add_parser("restore")
    rest.add_argument("project", nargs="?")
    rest.add_argument("-p", "--project", dest="project_flag")
    rest.add_argument("-i", "--index", type=int)

    # Simple Commands
    subparsers.add_parser("infra-down")
    subparsers.add_parser("clear-cache")
    subparsers.add_parser("doctor")
    subparsers.add_parser("list")
    subparsers.add_parser("prune")

    shell = subparsers.add_parser("shell")
    shell.add_argument("project", nargs="?")
    shell.add_argument("service", nargs="?")
    shell.add_argument("-p", "--project", dest="project_flag")

    gogo = subparsers.add_parser("gogo")
    gogo.add_argument("project", nargs="?")
    gogo.add_argument("-p", "--project", dest="project_flag")

    scale = subparsers.add_parser("scale")
    scale.add_argument("project", nargs="?")
    scale.add_argument("service_scale", nargs="+")
    scale.add_argument("-p", "--project", dest="project_flag")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    project_id = getattr(args, "project", None) or getattr(args, "project_flag", None)
    manager = LiferayManager(args)

    docker_required = [
        "run",
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
    ]
    if args.command in docker_required and not manager.check_docker():
        UI.die("Docker not accessible.")

    # Execution map
    cmds = {
        "run": lambda: manager.cmd_run(),
        "import": lambda: manager.cmd_import(args.source),
        "monitor": lambda: manager.cmd_monitor(args.source),
        "stop": lambda: manager.cmd_stop(project_id, getattr(args, "service", None)),
        "restart": lambda: manager.cmd_restart(
            project_id, getattr(args, "service", None)
        ),
        "down": lambda: manager.cmd_down(project_id, getattr(args, "service", None)),
        "logs": lambda: manager.cmd_logs(project_id, getattr(args, "service", None)),
        "deploy": lambda: manager.cmd_deploy(
            project_id, getattr(args, "service", None)
        ),
        "env": lambda: manager.cmd_env(project_id),
        "snapshot": lambda: manager.cmd_snapshot(project_id),
        "restore": lambda: manager.cmd_restore(project_id),
        "infra-down": lambda: manager.cmd_infra_down(),
        "clear-cache": lambda: manager.cmd_clear_cache(),
        "doctor": lambda: manager.cmd_doctor(project_id),
        "list": lambda: manager.cmd_list(),
        "shell": lambda: manager.cmd_shell(project_id, getattr(args, "service", None)),
        "gogo": lambda: manager.cmd_gogo(project_id),
        "scale": lambda: manager.cmd_scale(project_id, args.service_scale),
        "prune": lambda: manager.cmd_prune(),
    }

    if args.command in cmds:
        cmds[args.command]()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
