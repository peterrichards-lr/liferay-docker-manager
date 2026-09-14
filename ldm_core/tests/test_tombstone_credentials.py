"""A removal archive must not carry credentials unless asked (LDM-#1703).

The tombstone keeps a deleted project's configuration so it can be rebuilt, and
credentials are genuinely part of that -- a password set six months ago is
exactly the irreproducible thing the archive exists for. But keeping them means
`~/.ldm/removed/*.tar.gz` holds plaintext database and admin passwords for the
last fifty deleted projects, indefinitely, at whatever the umask happens to be.

So the default is to redact, and the choice is the user's: `--keep-credentials`
for one command, `ldm config set tombstone_keep_credentials true` for good. Opting
in is opting into responsibility for where that file lives, and LDM says so.

Two things these tests pin that are easy to get wrong:

* **The live project is never modified.** Redaction streams from memory into the
  tar. Editing the files on disk would strip the user's passwords out of a
  project that still exists -- and a deletion aborted afterwards would leave them
  with a stripped project and no archive.
* **A file that cannot be redacted is omitted, not passed through.** A missing
  member is recoverable; a leaked password is not.

`LDM_HOME` isolation is not optional here (LDM-#1715): before `tombstone_dir()`
honoured it, tests in this area wrote archives into the developer's real
`~/.ldm/removed`. Eight were found there.
"""

import json
import tarfile

import pytest

from ldm_core.utils import (
    TOMBSTONE_REDACTION,
    archive_project_config,
    redact_meta_secrets,
    redact_properties_secrets,
)

DB_SECRET = "SUPERSECRET_DB"  # pragma: allowlist secret
ADMIN_SECRET = "SUPERSECRET_ADMIN"  # pragma: allowlist secret
PE_SECRET = "SUPERSECRET_PE"  # pragma: allowlist secret
JDBC_SECRET = "SUPERSECRET_JDBC"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    home = tmp_path / "ldm-home"
    home.mkdir()
    monkeypatch.setenv("LDM_HOME", str(home))
    return home / ".ldm" / "removed"


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "files").mkdir(parents=True)
    (root / "routes").mkdir()
    root.joinpath("meta").write_text(
        json.dumps(
            {
                "container_name": "proj",
                "jdbc_pass": DB_SECRET,
                "credentials": [
                    {"type": "admin", "email": "a@b.c", "password": ADMIN_SECRET}
                ],
            }
        )
    )
    root.joinpath("files", "portal-ext.properties").write_text(
        f"default.admin.password={PE_SECRET}\n"
        f"jdbc.default.password={JDBC_SECRET}\n"
        "some.other=keepme\n"
        f"#default.admin.password={PE_SECRET}\n"
    )
    return root


def _contents(archive):
    """Every regular member's text, concatenated.

    `extractfile` returns None for a member with no payload, which mypy narrows
    on -- and which a directory entry legitimately is.
    """
    chunks = []
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            chunks.append(handle.read().decode())
    return "\n".join(chunks)


class TestRedactedByDefault:
    def test_no_secret_reaches_the_archive(self, project):
        body = _contents(archive_project_config(project))

        for secret in (DB_SECRET, ADMIN_SECRET, PE_SECRET, JDBC_SECRET):
            assert secret not in body, f"{secret} leaked into the tombstone"

    def test_the_marker_explains_itself(self, project):
        """`***` would leave someone wondering if that was the password."""
        body = _contents(archive_project_config(project))

        assert TOMBSTONE_REDACTION in body
        assert "re-enter" in TOMBSTONE_REDACTION

    def test_everything_else_survives(self, project):
        """Redacting is not an excuse to lose the configuration."""
        body = _contents(archive_project_config(project))

        assert "keepme" in body
        assert "container_name" in body
        assert "a@b.c" in body, "the email says which account to reset"

    def test_the_archive_is_not_world_readable(self, project):
        archive = archive_project_config(project)

        assert oct(archive.stat().st_mode)[-3:] == "600"

    def test_the_live_project_is_untouched(self, project):
        """The project still exists; stripping it would be data loss."""
        before = project.joinpath("meta").read_text()

        archive_project_config(project)

        assert project.joinpath("meta").read_text() == before
        assert DB_SECRET in project.joinpath("meta").read_text()
        assert (
            PE_SECRET in project.joinpath("files", "portal-ext.properties").read_text()
        )


class TestKeptWhenAskedFor:
    def test_the_opt_in_keeps_them(self, project):
        body = _contents(archive_project_config(project, keep_credentials=True))

        assert DB_SECRET in body
        assert ADMIN_SECRET in body
        assert PE_SECRET in body

    def test_it_is_still_not_world_readable(self, project):
        """Opting in is not opting out of a sane file mode."""
        archive = archive_project_config(project, keep_credentials=True)

        assert oct(archive.stat().st_mode)[-3:] == "600"


class TestMetaRedaction:
    def test_jdbc_pass_goes(self):
        out = json.loads(redact_meta_secrets(json.dumps({"jdbc_pass": DB_SECRET})))

        assert out["jdbc_pass"] == TOMBSTONE_REDACTION

    def test_only_the_password_goes_from_a_credential(self):
        raw = json.dumps(
            {
                "credentials": [
                    {"type": "admin", "email": "a@b.c", "password": ADMIN_SECRET}
                ]
            }
        )

        entry = json.loads(redact_meta_secrets(raw))["credentials"][0]

        assert entry["password"] == TOMBSTONE_REDACTION
        assert entry["email"] == "a@b.c"
        assert entry["type"] == "admin"

    def test_an_unparseable_meta_is_still_redacted(self):
        """Failing open would ship the secret because the file was odd."""
        raw = '{"jdbc_pass": "' + DB_SECRET + '", trailing junk'

        out = redact_meta_secrets(raw)

        assert DB_SECRET not in out

    def test_an_absent_key_is_not_invented(self):
        out = json.loads(redact_meta_secrets(json.dumps({"port": "8080"})))

        assert out == {"port": "8080"}


class TestPropertiesRedaction:
    def test_the_secret_values_go(self):
        out = redact_properties_secrets(
            f"default.admin.password={PE_SECRET}\njdbc.default.password={JDBC_SECRET}\n"
        )

        assert PE_SECRET not in out
        assert JDBC_SECRET not in out
        assert out.count(TOMBSTONE_REDACTION) == 2

    def test_other_properties_and_their_order_survive(self):
        out = redact_properties_secrets(
            f"first=1\ndefault.admin.password={PE_SECRET}\nlast=2\n"
        )
        lines = out.splitlines()

        assert lines[0] == "first=1"
        assert lines[2] == "last=2"

    def test_a_commented_secret_is_redacted_too(self):
        """ "It was commented out" is no comfort to whoever finds the archive.

        This asserted the opposite until `test_no_secret_reaches_the_archive`
        contradicted it. A disabled property is still a plaintext password on
        disk; the comment marker is kept so the line stays disabled.
        """
        out = redact_properties_secrets(f"#default.admin.password={PE_SECRET}\n")

        assert PE_SECRET not in out
        assert out.startswith("#"), "the line must stay commented out"
        assert TOMBSTONE_REDACTION in out

    def test_comments_and_blank_lines_survive(self):
        raw = "# a comment\n\nreal=1\n"

        assert redact_properties_secrets(raw) == raw


class TestTheStoreHonoursLdmHome:
    """LDM-#1715: it used Path.home(), so LDM_HOME did not reach it."""

    def test_the_archive_lands_under_ldm_home(self, project, _isolate):
        archive = archive_project_config(project, timestamp="20260914-000000")

        assert archive.parent == _isolate, (
            "the tombstone ignored LDM_HOME -- tests and sudo runs both write "
            "to the wrong home"
        )
