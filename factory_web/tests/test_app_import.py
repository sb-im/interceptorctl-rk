import unittest

try:
    import fastapi  # noqa: F401
except ImportError:
    fastapi = None


@unittest.skipUnless(fastapi is not None, "FastAPI is not installed in this environment")
class AppImportTests(unittest.TestCase):
    def test_expected_routes_are_registered_without_running_commands(self):
        import app

        paths = {route.path for route in app.app.routes}
        self.assertIn("/", paths)
        self.assertIn("/api/info", paths)
        self.assertIn("/api/command", paths)
        self.assertIn("/api/status", paths)


if __name__ == "__main__":
    unittest.main()
