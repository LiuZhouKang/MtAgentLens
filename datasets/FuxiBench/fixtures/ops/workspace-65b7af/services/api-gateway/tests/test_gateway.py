"""Tests for api-gateway route matching."""
import unittest

from src.gateway import ROUTES, match_route, normalize_path


class RouteTests(unittest.TestCase):
    def test_normalize_collapses_separators_and_trims_trailing_slash(self):
        self.assertEqual(normalize_path("//v1///orders/"), "/v1/orders")

    def test_normalize_empty_path_is_root(self):
        self.assertEqual(normalize_path(""), "/")

    def test_match_route_is_method_case_insensitive(self):
        self.assertEqual(match_route(ROUTES, "get", "/health"), "health")

    def test_unknown_route_returns_404(self):
        self.assertEqual(match_route(ROUTES, "GET", "/v1/missing"), "404")


if __name__ == "__main__":
    unittest.main()
