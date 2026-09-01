"""`ldm completion` must actually print its instructions (LDM-#1504).

The command exists solely to tell the user what to add to their shell profile.
Every explanatory line was written with `UI.detail`, which only emits under
`--info`/`--verbose`, so the default output was two bare snippets, no
explanation, and a dangling "for the changes to take effect" whose opening
half had been suppressed:

    === LDM Shell Completion ===

        eval "$(ldm completion zsh)"

        export MANPATH="$MANPATH:$HOME/.ldm/man"

    for the changes to take effect.

On an unsupported shell it was worse: one suppressed line then `return`, so the
user got a heading and silence.
"""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from ldm_core.diagnostics.completions import run_completion
from ldm_core.ui import UI


def _capture(shell):
    """Bare `ldm completion` output at default verbosity.

    target_shell must stay None: passing it is the `ldm completion zsh` form,
    which emits the raw script for eval rather than the guidance. The shell is
    detected from $SHELL, so that is what the test controls.
    """
    UI.reset()
    buf = io.StringIO()
    with patch.dict("os.environ", {"SHELL": f"/bin/{shell}"}), redirect_stdout(buf):
        run_completion(MagicMock())
    return buf.getvalue()


class TestGuidanceIsVisibleByDefault(unittest.TestCase):
    def setUp(self):
        self.addCleanup(UI.reset)

    def test_zsh_explains_what_the_snippet_is_for(self):
        out = _capture("zsh")
        self.assertIn("eval", out, "the snippet itself must still print")
        self.assertIn(
            "tab-completion",
            out,
            "the snippet printed with nothing explaining it (LDM-#1504)",
        )

    def test_manpath_line_is_explained(self):
        out = _capture("zsh")
        self.assertIn("MANPATH", out)
        self.assertIn("man ldm", out, "the MANPATH export printed unexplained")

    def test_no_dangling_sentence_fragment(self):
        """The tail printed alone because its head was suppressed."""
        out = _capture("zsh")
        for line in out.splitlines():
            self.assertNotEqual(
                line.strip(),
                "for the changes to take effect.",
                "sentence fragment printed with its opening half suppressed",
            )

    def test_restart_advice_is_a_whole_sentence(self):
        out = _capture("zsh")
        self.assertIn("for the changes to take effect", out)
        self.assertIn("Restart your terminal", out)

    def test_compinit_ordering_is_warned_about(self):
        """Being after the first compinit is not sufficient.

        nvm's bash_completion, gcloud's completion.zsh.inc and several plugin
        managers re-run compinit, which rebuilds the completion table and
        discards earlier compdef registrations. Observed on a real machine:
        LDM's eval sat at .zshrc line 7, nvm at line 65, and `ldm` was
        unregistered in every interactive shell.
        """
        out = _capture("zsh")
        self.assertIn("compinit", out)

    def test_unsupported_shell_says_something(self):
        """Was a heading followed by silence."""
        out = _capture("tcsh")
        self.assertIn("tcsh", out)
        self.assertIn("optimized for", out)


if __name__ == "__main__":
    unittest.main()
