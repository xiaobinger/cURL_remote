import unittest
from curlgen.config import DEFAULT_DASHBOARD_LAYOUT, normalize_dashboard_layout


class DashboardLayoutTests(unittest.TestCase):
    def test_invalid_value_returns_independent_default(self):
        first = normalize_dashboard_layout(None)
        self.assertEqual(first, DEFAULT_DASHBOARD_LAYOUT)
        self.assertIsNot(first, DEFAULT_DASHBOARD_LAYOUT)
        first["items"][0]["width"] = 4
        self.assertNotEqual(first, DEFAULT_DASHBOARD_LAYOUT)

    def test_sanitizes_deduplicates_and_fills_missing_components(self):
        raw = {"version": 99, "items": [
            {"id": "calendar", "visible": False, "width": 7},
            {"id": "calendar", "visible": True, "width": 12},
            {"id": "unknown", "visible": True, "width": 12},
            {"id": "terminal", "visible": True, "width": 8},
        ]}
        result = normalize_dashboard_layout(raw)
        self.assertEqual(result["version"], 1)
        self.assertEqual([x["id"] for x in result["items"][:2]], ["calendar", "terminal"])
        self.assertEqual(result["items"][0], {"id": "calendar", "visible": False, "width": 4})
        self.assertEqual(len(result["items"]), 6)
        self.assertIn("market", [x["id"] for x in result["items"]])


if __name__ == "__main__":
    unittest.main()
