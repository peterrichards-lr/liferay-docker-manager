"""`ldm ai` with no query opens a session instead of failing (LDM-#1505).

The parser's help had always promised "Start an interactive troubleshooting
session", but `query` was a required positional, so the bare form -- the most
natural way to reach for it -- was the one that could not work:

    $ ldm ai
    ldm ai: error: the following arguments are required: query

Troubleshooting is multi-turn: the answer is usually a question back, or a
follow-up once a suggestion has been tried. Each follow-up used to be a fresh
process with no memory, so the user retyped context the model already had.
"""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from ldm_core.cli import get_parser
from ldm_core.handlers.ai import AiService
from ldm_core.ui import UI


class TestQueryIsOptional(unittest.TestCase):
    def test_bare_ldm_ai_parses(self):
        parser, _ = get_parser()
        args = parser.parse_args(["ai"])
        self.assertIsNone(args.query)

    def test_a_query_still_parses(self):
        parser, _ = get_parser()
        args = parser.parse_args(["ai", "why will my project not start"])
        self.assertEqual("why will my project not start", args.query)


class TestNonInteractiveRefusesRatherThanHanging(unittest.TestCase):
    """A REPL blocking on stdin in a pipeline is worse than the old error."""

    def setUp(self):
        UI.reset()
        self.addCleanup(UI.reset)
        self.svc = AiService(MagicMock())

    def test_refuses_when_stdin_is_not_a_tty(self):
        with (
            patch("sys.stdin.isatty", return_value=False),
            patch.object(UI, "die", side_effect=SystemExit(1)) as die,
            self.assertRaises(SystemExit),
        ):
            self.svc.cmd_ai(None)
        self.assertIn("needs a question", die.call_args[0][0])

    def test_refuses_under_non_interactive_flag(self):
        UI.NON_INTERACTIVE = True
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch.object(UI, "die", side_effect=SystemExit(1)),
            self.assertRaises(SystemExit),
        ):
            self.svc.cmd_ai(None)

    def test_a_query_is_still_accepted_non_interactively(self):
        """Scripts passing a question must keep working."""
        with (
            patch("sys.stdin.isatty", return_value=False),
            patch.object(AiService, "_chat_loop", return_value=None) as loop,
        ):
            self.svc.cmd_ai("a question")
        loop.assert_called_once()


class TestConversationState(unittest.TestCase):
    """History must survive between questions, or follow-ups are meaningless."""

    def setUp(self):
        UI.reset()
        self.addCleanup(UI.reset)
        self.svc = AiService(MagicMock())

    def _run_repl(self, inputs):
        """Drive the REPL with a scripted stdin, capturing the shared history."""
        seen = []

        async def fake_turn(messages, *_a, **_k):
            seen.append(list(messages))
            messages.append({"role": "model", "parts": [{"text": "answer"}]})

        with (
            patch.object(AiService, "_get_gemini_val", return_value="k"),
            patch.object(AiService, "_get_mcp_tools_schema", return_value=[]),
            patch.object(AiService, "_run_turn", side_effect=fake_turn),
            patch("builtins.input", side_effect=inputs),
        ):
            asyncio.run(self.svc._chat_loop(None))
        return seen

    def test_second_question_still_carries_the_first(self):
        seen = self._run_repl(["first", "second", "exit"])
        self.assertEqual(2, len(seen), "expected two questions to be answered")
        texts = [p["parts"][0].get("text") for p in seen[1]]
        self.assertIn("first", texts, "the first question was lost")
        self.assertIn("answer", texts, "the first ANSWER was lost")
        self.assertIn("second", texts)

    def test_exit_word_ends_the_session(self):
        self.assertEqual(1, len(self._run_repl(["only", "exit"])))

    def test_eof_ends_the_session_quietly(self):
        """Ctrl-D is how people leave a REPL; it is not an error."""
        self.assertEqual(1, len(self._run_repl(["only", EOFError()])))

    def test_blank_input_is_ignored_not_sent(self):
        self.assertEqual(1, len(self._run_repl(["", "  ", "real", "exit"])))


class TestRunTurnRecordsTheAnswer(unittest.TestCase):
    """The real _run_turn must append the model's answer to history.

    Exercised against _run_turn itself, not a stub: the REPL test above
    simulates the append, so on its own it would pass even if the production
    code dropped the answer -- which is exactly what it did before LDM-#1505,
    because a one-shot never needed the history again.
    """

    def setUp(self):
        UI.reset()
        self.addCleanup(UI.reset)
        self.svc = AiService(MagicMock())

    def _response(self, text):
        r = MagicMock()
        r.json.return_value = {
            "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]
        }
        r.raise_for_status.return_value = None
        return r

    def test_answer_is_appended_to_messages(self):
        # Annotated: inferred from the literal, mypy narrows the values to
        # Collection[str] and then refuses to index parts[0].
        messages: list[dict] = [{"role": "user", "parts": [{"text": "q"}]}]
        with patch(
            "ldm_core.handlers.ai.requests.post", return_value=self._response("A")
        ):
            asyncio.run(self.svc._run_turn(messages, "u", {}, {}, []))

        self.assertEqual(
            2, len(messages), "the answer was not kept, so a follow-up loses it"
        )
        self.assertEqual("model", messages[-1]["role"])
        self.assertEqual("A", messages[-1]["parts"][0]["text"])


if __name__ == "__main__":
    unittest.main()
