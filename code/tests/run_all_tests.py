"""Test runner orchestrator executing the complete INA v2 test suite.

Discovers and executes all unit tests, schema validators, policy checks,
kernel primitives, and MCP server contracts.
"""

import os
import sys
import unittest

# Ensure both workspace root and inav2 package directory are on sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_DIR = os.path.dirname(BASE_DIR)
for p in (WORKSPACE_DIR, BASE_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)


def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(os.path.join(BASE_DIR, "tests", "unit"), pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    if not result.wasSuccessful():
        print(f"\nFAILED: {len(result.failures)} failures, {len(result.errors)} errors.", file=sys.stderr)
        return 1

    print(f"\nSUCCESS: Ran {result.testsRun} tests cleanly.", file=sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
