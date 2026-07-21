import json
import os
import tarfile
import time
from datetime import datetime
from pathlib import Path
from typing import cast

from ldm_core.ui import UI
from ldm_core.utils import get_actual_home, safe_extract

class Custom_containersSnapshotService:
    def __init__(self, facade):
        self.facade = facade
        self.manager = facade.manager
        self.args = facade.manager.args

    def _snapshot_custom_containers(self, project_meta, snap_dir):
        custom_containers = project_meta.get("custom_containers")
        if custom_containers and isinstance(custom_containers, list):
            custom_images_dir = snap_dir / "custom_images"
            from ldm_core.utils import safe_mkdir

            safe_mkdir(custom_images_dir, parents=True, exist_ok=True)
            for container in custom_containers:
                image = container.get("image")
                c_name = container.get("service_name")
                if image and c_name:
                    UI.info(f"Saving custom image {image} for service {c_name}...")
                    image_tar = custom_images_dir / f"{c_name}.tar"
                    try:
                        res = self.manager.run_command(
                            ["docker", "save", image, "-o", str(image_tar)], check=False
                        )
                        if res is None:
                            UI.warning(
                                f"Failed to save custom image {image}. It may not exist locally."
                            )
                    except Exception as e:
                        UI.warning(f"Failed to save custom image {image}: {e}")

    def _restore_custom_images(self, choice_path):
        custom_images_dir = choice_path / "custom_images"
        if custom_images_dir.exists() and custom_images_dir.is_dir():
            UI.info("Loading custom container images from snapshot...")
            for tar_file in custom_images_dir.glob("*.tar"):
                UI.detail(f"  + Loading image from {tar_file.name}...")
                try:
                    self.manager.run_command(["docker", "load", "-i", str(tar_file)])
                except Exception as e:
                    UI.warning(f"Failed to load custom image {tar_file.name}: {e}")

