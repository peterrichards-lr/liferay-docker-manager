# Cascading Defaults Plan

## Objective

Implement a robust cascading defaults system and the `ldm defaults` command to manage LDM's configuration settings. This ensures consistent, reproducible environments while offering flexibility across system, user, and project levels.

## Levels of Cascading Defaults

1. **Convention Defaults**: Hardcoded in `ldm_core/constants.py` (e.g., `tag`, `db`, `search`). Immutable.
2. **Global Defaults**: Stored at `/etc/ldm/defaults.json` (or similar system path). Modifiable by sysadmins.
3. **User Defaults**: Stored at `~/.ldm/defaults.json` (or `~/.ldmrc`). Modifiable by the user.
4. **Project Meta**: Stored in `.liferay-docker.meta` upon project creation. This "freezes" the resolved defaults for the project.

## `ldm defaults` Command

- `ldm defaults`: Displays a tree/table showing the resolved values and their source (Convention, Global, User).
- `ldm defaults <key> <value> [--global]`: Sets a default at the User or Global level.
- `ldm defaults --remove <key> [--global]`: Removes a custom default.

## Implementation Steps

1. **Define Core Defaults**: Formalize the convention defaults in `ldm_core/config_manager.py` (new) or `constants.py`.
2. **Implement Resolution Logic**: Create a class/function that merges Convention -> Global -> User -> Project.
3. **Integrate with CLI Prompts**: Modify `ldm init`, `ldm run`, and other interactive prompts to use the resolved defaults.
4. **Freeze on Creation**: Ensure `ldm init` writes these resolved values into the project's metadata so they persist if defaults change later.
5. **Build `ldm defaults` CLI**: Implement the command in `cli.py` and `handlers/diagnostics.py` or `handlers/config.py`.
