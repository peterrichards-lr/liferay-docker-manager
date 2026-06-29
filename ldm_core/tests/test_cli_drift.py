import argparse
import unittest
from unittest.mock import patch

from ldm_core.cli import get_parser
from ldm_core.utils import get_all_options


class TestCliDrift(unittest.TestCase):
    def test_extract_cli_options(self):
        parser, _ = get_parser()
        options = get_all_options(parser)

        # Assert some core known flags are extracted
        self.assertIn("-v", options)
        self.assertIn("--verbose", options)
        self.assertIn("--force-downgrade", options)

    def test_drift_check_passes_when_documented(self):
        # Create a dummy parser
        parser = argparse.ArgumentParser()
        parser.add_argument("-t", "--tag", help="Liferay tag")

        # Mock reading doc file content with the tags documented
        doc_content = "To specify the tag, use the -t or --tag option."

        with patch("pathlib.Path.read_text", return_value=doc_content):
            missing = []
            options = get_all_options(parser)
            options.discard("-h")
            options.discard("--help")
            for opt in options:
                if opt not in doc_content:
                    missing.append(opt)
            self.assertEqual(len(missing), 0)

    def test_drift_check_fails_when_undocumented(self):
        # Create a dummy parser
        parser = argparse.ArgumentParser()
        parser.add_argument("-t", "--tag", help="Liferay tag")
        parser.add_argument("--secret-flag", help="Undocumented")

        # Mock reading doc file content missing --secret-flag
        doc_content = "To specify the tag, use the -t or --tag option."

        with patch("pathlib.Path.read_text", return_value=doc_content):
            missing = []
            options = get_all_options(parser)
            options.discard("-h")
            options.discard("--help")
            for opt in options:
                if opt not in doc_content:
                    missing.append(opt)
            self.assertIn("--secret-flag", missing)
