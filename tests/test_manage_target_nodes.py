"""Unit tests for standalone target node power management script (scripts/manage_target_nodes.py).

Last Updated: 2026-08-20 | Last Reviewed: 2026-08-20
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from scripts.manage_target_nodes import (
    is_in_shutdown_window,
    parse_duration,
)


class TestManageTargetNodes(unittest.TestCase):
    def test_parse_duration(self) -> None:
        """Test parse_duration converts strings like '2h', '30m', '1d'."""
        self.assertEqual(parse_duration("2h"), timedelta(hours=2))
        self.assertEqual(parse_duration("30m"), timedelta(minutes=30))
        self.assertEqual(parse_duration("1d"), timedelta(days=1))
        # Fallback default
        self.assertEqual(parse_duration("invalid"), timedelta(hours=2))

    def test_is_in_shutdown_window_overnight(self) -> None:
        """Test overnight shutdown window detection (19:00 - 07:00)."""
        # Tuesday 20:00 (Overnight)
        dt_night = datetime(2026, 8, 18, 20, 0, 0)
        self.assertTrue(is_in_shutdown_window(dt_night, "overnight"))
        self.assertTrue(is_in_shutdown_window(dt_night, "auto"))

        # Tuesday 12:00 (Daytime)
        dt_day = datetime(2026, 8, 18, 12, 0, 0)
        self.assertFalse(is_in_shutdown_window(dt_day, "overnight"))
        self.assertFalse(is_in_shutdown_window(dt_day, "auto"))

    def test_is_in_shutdown_window_weekend(self) -> None:
        """Test weekend shutdown window detection (Fri 19:00 - Mon 07:00)."""
        # Saturday 14:00 (Weekend)
        dt_sat = datetime(2026, 8, 22, 14, 0, 0)
        self.assertTrue(is_in_shutdown_window(dt_sat, "weekend"))
        self.assertTrue(is_in_shutdown_window(dt_sat, "auto"))

        # Wednesday 14:00 (Weekday Daytime)
        dt_wed = datetime(2026, 8, 19, 14, 0, 0)
        self.assertFalse(is_in_shutdown_window(dt_wed, "weekend"))

    def test_schedule_off(self) -> None:
        """Test schedule 'off' disables shutdown window enforcement."""
        dt_sat = datetime(2026, 8, 22, 14, 0, 0)
        self.assertFalse(is_in_shutdown_window(dt_sat, "off"))


if __name__ == "__main__":
    unittest.main()
