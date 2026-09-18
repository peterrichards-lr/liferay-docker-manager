"""Verification for the pinned container MAC (LDM-#1752, #1798, #1799).

Liferay binds a licence to a MAC address, so LDM can pin the container's MAC to
the node's NIC. Writing `mac_address` into the compose file is a *request*; this
module is the confirmation.

## Why this is its own module

The check originally lived in the `ldm run` pipeline and ran nowhere else. That
turned out to be the one place a mismatch cannot occur: `mac_address` is part of
the compose *service spec*, so changing the configured value makes compose
recreate the container by itself. Measured on a real node (LDM-#1798):

    # changed the configured MAC, ran `ldm run` with NO `ldm rm`
    container Created 12:40:56Z   MAC 02:aa:bb:cc:dd:ee   (the new value)

So the refusal branch had never executed, while the routes that genuinely leave
a stale container -- `ldm start`, `ldm restart` -- never ran the check at all.
It lives here so both the pipeline and the lifecycle commands can call it
without importing each other.

## The two distinct failures

`actual != expected` is "the pin did not take" -- a toolchain that ignored the
form LDM writes, or a container older than the configured value. That is an
infrastructure failure and it **refuses** (exit 3).

`actual == expected`, but the value is on none of the node's interfaces, is a
different thing: the pin took perfectly and the *configured value is wrong*.
Liferay then logs `MAC address matching failed` and serves the Activation page,
with a healthy container and `License registered` in the log -- the original
LDM-#1752 symptom, which the old check could not see because it never consulted
the node. That **warns** rather than refuses, for the same reason LDM-#1780
warns: Liferay validates the container's MAC against the licence and does not
care what the host's interfaces are, so a licence bound to a MAC that is not a
current NIC is legitimate, if unusual.
"""

from __future__ import annotations

from ldm_core.ui import UI


def configured_mac(target_name: str | None) -> str:
    """The MAC pinned for a target, or "" when there is none to check.

    Empty for the local target and for any node without a pin -- this exists to
    catch a pin that did not take, not to require one.
    """
    if not target_name or target_name == "local":
        return ""
    try:
        from ldm_core.config import load_targets

        node = load_targets().get(target_name)
    except Exception:
        return ""
    return (getattr(node, "mac_address", "") or "").strip().lower() if node else ""


def verify_pinned_mac(
    project_meta,
    target_name: str | None,
    *,
    dry_run: bool = False,
    recreate_hint: str = "ldm run",
    check_interfaces: bool = True,
) -> None:
    """Confirm the container carries the MAC that was configured.

    `recreate_hint` is the command that would actually fix a stale container
    from where the caller stands. It is a parameter because the right advice
    differs: from `ldm start`/`ldm restart` a plain `ldm run` recreates the
    container, so telling the operator to `ldm rm` first would be destructive
    advice for a problem that does not need it.
    """
    if dry_run:
        return

    expected = configured_mac(target_name)
    if not expected:
        return

    from ldm_core.docker_service import DockerService
    from ldm_core.utils import liferay_container_of

    container = liferay_container_of(project_meta)
    actual = DockerService.container_mac_address(container, target_name)

    if actual is None:
        # Unreadable is not the same as wrong. Say so and continue rather than
        # blocking on a diagnosis that did not run.
        UI.warning(
            f"Could not read the MAC of '{container}' to confirm it was pinned "
            f"(LDM-#1752). Liferay's licence binds to it, so if activation "
            f"fails, check it by hand."
        )
        return

    if actual != expected:
        UI.die(
            f"The container's MAC is {actual}, not the {expected} configured "
            f"for node '{target_name}'.",
            details=(
                "Liferay's licence binds to the MAC. Left alone, this boots a "
                "healthy container that logs 'License registered' and then "
                "serves the DXP Activation page instead of the Sign In form."
            ),
            tip=(
                "The MAC can only be set when a container is created, so a "
                "running one cannot be corrected in place. Recreate it:\n"
                f"    {recreate_hint}"
            ),
            exit_code=3,
        )

    # LDM-#1804: the interface cross-check is an SSH round trip, measured at
    # ~1.4s against a real node. That is noise inside `ldm run` (a multi-minute
    # boot) and 48% of an `ldm restart`, which took 2.9s unpinned and 4.3s
    # pinned. So it is on by default where the container is created and off on
    # the lifecycle fast path, where `--verify-mac` opts back in.
    #
    # Little is lost: a wrong pin is caught at `ldm target add` (LDM-#1780),
    # which runs once rather than on every restart. What this catches is the
    # narrower case of a node whose interfaces changed after registration.
    if check_interfaces:
        _warn_if_not_a_node_interface(expected, target_name)
    UI.detail(f"Container MAC pinned to {actual} as configured for '{target_name}'.")


def _warn_if_not_a_node_interface(expected: str, target_name: str | None) -> None:
    """LDM-#1798: the pin took, but is it the right address?

    `actual == expected` only proves compose did as it was told. It says nothing
    about whether the configured value is a MAC the licence will accept, and
    that was the gap: a wrong-but-applied pin passed silently and the operator
    discovered it twenty minutes later on the Activation page.

    Silent when the node cannot be asked -- unreachable is not wrong, the same
    distinction drawn above for an unreadable container MAC.
    """
    if not target_name:
        return

    try:
        from ldm_core.config import load_targets, remote_interface_macs

        node = load_targets().get(target_name)
        if node is None:
            return
        interfaces = remote_interface_macs(node)
    except Exception:
        return

    if not interfaces or expected in interfaces.values():
        return

    listing = ", ".join(f"{n} {m}" for n, m in sorted(interfaces.items()))
    UI.warning(
        f"The pin was applied, but {expected} is on none of '{target_name}'s "
        f"interfaces ({listing})."
    )
    UI.detail(
        "Liferay validates the container's MAC against the licence, so if this "
        "is not the address the licence was issued for it will log 'MAC "
        "address matching failed' and serve the Activation page -- with a "
        "healthy container and 'License registered' in the log."
    )
