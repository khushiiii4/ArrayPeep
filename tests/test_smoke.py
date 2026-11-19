"""Smoke test: confirms the Python test runner discovers and runs tests."""
import sys
import unittest


class SmokeTest(unittest.TestCase):
    def test_python_version_and_arithmetic(self) -> None:
        self.assertGreaterEqual(sys.version_info[:2], (3, 10))
        self.assertEqual(1 + 1, 2)


if __name__ == "__main__":
    unittest.main()
