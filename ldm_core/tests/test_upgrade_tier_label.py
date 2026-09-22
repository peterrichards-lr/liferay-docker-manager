"""`ldm system upgrade` labels the INSTALLED version, not the search channel.

LDM-#1912: `tier` was chosen from `pre_release` -- the `--beta` flag -- so the
label reported how the user searched rather than what they had. A user on
stable v2.25.0 who passed `--beta` was told they were on a pre-release.

The matrix matters. Two of these four cells pass against the unfixed code, so
a test asserting only one row would prove nothing; the flag-varying pairs are
the ones that fail.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_CAPTURED: list[str] = []


def _capture(message, *_args, **_kwargs):
    """Records a UI.success line. A named function, not a lambda:
    ruff flags the unused *args/**kwargs a lambda needs here."""
    _CAPTURED.append(message)


def _run(version, beta):
    """Returns the success line `run_upgrade` prints for an up-to-date install."""
    from ldm_core.diagnostics import upgrade as up

    # A real namespace, not a MagicMock: `run_upgrade` reads its flags with
    # `getattr(args, name, False)`, and every attribute of a MagicMock is a
    # truthy mock -- which sends it down the `--repair` branch and exits 1
    # before it ever reaches the label.
    handler = MagicMock()
    handler.manager.args = SimpleNamespace(
        pre_release=beta, check_only=False, version=None, repair=False
    )

    _CAPTURED.clear()
    with (
        patch.object(up, "VERSION", version),
        patch.object(up, "check_for_updates", return_value=(version, "http://x")),
        patch.object(up.UI, "success", side_effect=_capture),
        patch.object(up.UI, "detail"),
        patch.object(up.UI, "error"),
    ):
        up.run_upgrade(handler)
    return " ".join(_CAPTURED)


class TestTheLabelDescribesTheInstalledVersion(unittest.TestCase):
    def test_stable_without_the_flag(self):
        self.assertIn("(stable)", _run("2.25.0", beta=False))

    def test_stable_stays_stable_with_the_flag(self):
        """--beta widens the search; it does not change what is installed."""
        said = _run("2.25.0", beta=True)
        self.assertIn("(stable)", said)
        self.assertNotIn("(pre-release)", said)

    def test_prerelease_with_the_flag(self):
        self.assertIn("(pre-release)", _run("2.26.0-pre.1", beta=True))

    def test_prerelease_stays_prerelease_without_the_flag(self):
        """The inverse error: a genuine pre-release reported as stable."""
        said = _run("2.26.0-pre.1", beta=False)
        self.assertIn("(pre-release)", said)
        self.assertNotIn("(stable)", said)

    def test_the_label_does_not_vary_with_the_flag(self):
        """States the invariant directly, so a future refactor cannot
        reintroduce the bug while keeping each row above passing."""
        for version in ("2.25.0", "2.26.0-pre.1"):
            with self.subTest(version=version):
                self.assertEqual(_run(version, False), _run(version, True))


if __name__ == "__main__":
    unittest.main()
