import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from ldm_core.ui import UI


def cmd_set_version(self, product_key):
    """Updates the workspace gradle.properties liferay.workspace.product version."""
    project_name = getattr(self.manager.args, "project", None)
    project_path = self.manager.detect_project_path(project_name)
    if not project_path or not project_path.exists():
        UI.die("Could not resolve project path.")
        return

    gradle_props = project_path / "gradle.properties"
    if not gradle_props.exists():
        gradle_props = project_path / "liferay" / "gradle.properties"

    if not gradle_props.exists():
        UI.die(
            f"Could not find a valid gradle.properties in the workspace: {project_path}"
        )
        return

    UI.heading(f"Updating Workspace Version for: {project_path.name}")
    content = gradle_props.read_text(encoding="utf-8")

    if "liferay.workspace.product" not in content:
        UI.die(
            f"The property 'liferay.workspace.product' was not found in {gradle_props.name}"
        )
        return

    # Update the property

    new_content = re.sub(
        r"liferay\.workspace\.product\s*=\s*[^\r\n]+",
        f"liferay.workspace.product={product_key}",
        content,
    )

    gradle_props.write_text(new_content, encoding="utf-8")

    # Verify compatibility
    UI.info(
        f"Updated liferay.workspace.product to {product_key} in {gradle_props.name}"
    )

    from ldm_core.utils import resolve_liferay_docker_tag

    resolved_tag, is_portal = resolve_liferay_docker_tag(product_key, self.manager)
    if not resolved_tag:
        UI.warning(
            f"Could not cleanly resolve a Docker tag for '{product_key}'. Falling back to stripping prefix."
        )
        tag = re.sub(r"^(dxp|portal)-", "", product_key)
    else:
        tag = resolved_tag

    # Update meta explicitly to trigger restart warnings if running
    meta = self.manager.read_meta(project_path)
    meta["tag"] = tag
    if resolved_tag:
        meta["portal"] = "true" if is_portal else "false"
    self.manager.write_meta(project_path, meta)

    UI.success(
        f"Successfully bumped workspace version to {product_key} (Mapped Tag: {tag})."
    )
    UI.info("To apply this upgrade to the running environment, execute:")
    print(f"    {UI.BYELLOW}ldm restart --upgrade-db{UI.COLOR_OFF}")
