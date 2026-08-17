from ldm_core.ui import UI


class CustomContainersSnapshotService:
    def __init__(self, facade):
        self.facade = facade
        self.manager = facade.manager
        self.args = facade.manager.args

    def _resolve_docker_prefix(self, project_meta=None):
        """Resolves the docker CLI prefix for the project's active target.

        `docker save`/`docker load` stream image data through the Docker
        API to whichever daemon `--context` points at -- the `-o`/`-i` tar
        file argument is always read/written on the machine running the
        CLI, so no path-remapping is needed, only the `--context` prefix.
        See docs/explanation/remote-node-architecture.md.
        """
        target_name = getattr(self.manager, "target", None) or (
            project_meta.get("target") if isinstance(project_meta, dict) else None
        )
        from ldm_core.docker_service import DockerService

        return DockerService.get_docker_cmd_prefix(target_name)

    def _snapshot_custom_containers(self, project_meta, snap_dir):
        custom_containers = project_meta.get("custom_containers")
        if custom_containers and isinstance(custom_containers, list):
            custom_images_dir = snap_dir / "custom_images"
            from ldm_core.utils import safe_mkdir

            safe_mkdir(custom_images_dir, parents=True, exist_ok=True)
            docker_prefix = self._resolve_docker_prefix(project_meta)
            for container in custom_containers:
                image = container.get("image")
                c_name = container.get("service_name")
                if image and c_name:
                    UI.detail(f"Saving custom image {image} for service {c_name}...")
                    image_tar = custom_images_dir / f"{c_name}.tar"
                    try:
                        res = self.manager.run_command(
                            [*docker_prefix, "save", image, "-o", str(image_tar)],
                            check=False,
                        )
                        if res is None:
                            UI.warning(
                                f"Failed to save custom image {image}. It may not exist locally."
                            )
                    except Exception as e:
                        UI.warning(f"Failed to save custom image {image}: {e}")

    def _restore_custom_images(self, choice_path, project_meta=None):
        custom_images_dir = choice_path / "custom_images"
        if custom_images_dir.exists() and custom_images_dir.is_dir():
            UI.detail("Loading custom container images from snapshot...")
            docker_prefix = self._resolve_docker_prefix(project_meta)
            for tar_file in custom_images_dir.glob("*.tar"):
                UI.detail(f"  + Loading image from {tar_file.name}...")
                try:
                    self.manager.run_command(
                        [*docker_prefix, "load", "-i", str(tar_file)]
                    )
                except Exception as e:
                    UI.warning(f"Failed to load custom image {tar_file.name}: {e}")
