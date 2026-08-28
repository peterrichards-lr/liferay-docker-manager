"""The per-project CREATE DATABASE must honour the active target (LDM-#1401).

The block that creates a project's database inside the global cluster used the
bare `docker` executable, while every neighbouring call resolves a prefix via
`DockerService.get_docker_cmd_prefix(target_name)`.

For a project on a remote target that meant:

  - `infra.setup_global_database` created the global container on the REMOTE
    engine (it resolves the prefix correctly), but
  - this `CREATE DATABASE` ran against the LOCAL daemon.

The existence check runs with `check=False`, so a local daemon lacking that
container returns nothing and the code proceeds to the create -- which fails, if
at all, with an error naming the wrong daemon.

Shared *database* on a remote target is supported: `setup_global_search`'s
docstring records that the first-time remote limitation is specific to *search*,
whose data directories are host bind-mounts, and contrasts it with the
database's Docker-managed named volume.

LDM-#1361 doubled the exposure -- the MySQL branch it added copied the same
literal -- so there were four hardcoded calls, not two.
"""

import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "pipelines" / "run.py"


def _shared_db_block():
    """The source of the `use_shared_db` provisioning block."""
    text = SOURCE.read_text()
    start = text.index('if shutil.which("docker") and use_shared_db and not no_up:')
    end = text.index("exists_check = manager.run_command(", start)
    return text[start:end]


class TestSharedDatabaseHonoursTarget(unittest.TestCase):
    def test_no_bare_docker_literal_in_the_shared_db_block(self):
        """A bare "docker" here silently targets the local daemon."""
        block = _shared_db_block()
        self.assertNotIn(
            '"docker",',
            block,
            "the shared-database CREATE/check commands use a bare `docker` "
            "executable; on a remote target the global container lives on the "
            "remote engine while this runs locally (LDM-#1401).",
        )

    def test_the_block_resolves_a_prefix(self):
        block = _shared_db_block()
        self.assertIn("get_docker_cmd_prefix", block)
        self.assertIn("*docker_prefix,", block)

    def test_both_engines_are_covered(self):
        """MySQL and PostgreSQL each contribute a check and a create."""
        block = _shared_db_block()
        self.assertEqual(
            4,
            block.count("*docker_prefix,"),
            "expected four prefixed calls -- check+create for MySQL and for "
            "PostgreSQL. LDM-#1361 added the MySQL pair; a new engine branch "
            "must prefix its calls too.",
        )

    def test_run_py_still_parses(self):
        ast.parse(SOURCE.read_text())


if __name__ == "__main__":
    unittest.main()
