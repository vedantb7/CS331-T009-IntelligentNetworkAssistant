"""Unit tests for role tokens (policy/authorization.py)."""

from __future__ import annotations

import unittest

from policy.authorization import (
    AuthConfigError, TokenAuthenticator, hash_token,
)

ADMIN = "admin-secret-value-123"
OPERATOR = "operator-secret-value-456"


def make() -> TokenAuthenticator:
    return TokenAuthenticator({"admin": hash_token(ADMIN), "operator": hash_token(OPERATOR)})


class TestAuthentication(unittest.TestCase):
    def test_valid_tokens_map_to_roles(self):
        auth = make()
        self.assertEqual(auth.authenticate(ADMIN).role, "admin")
        self.assertEqual(auth.authenticate(OPERATOR).role, "operator")

    def test_invalid_tokens_rejected(self):
        auth = make()
        for bad in (None, "", " ", "wrong", ADMIN + "x", ADMIN.upper(), 123, b"admin", ["x"], "a" * 1000):
            self.assertIsNone(auth.authenticate(bad), bad)

    def test_principal_label_hides_the_token(self):
        principal = make().authenticate(OPERATOR)
        self.assertNotIn(OPERATOR, principal.label)
        self.assertTrue(principal.label.startswith("operator:"))

    def test_hash_is_not_the_token(self):
        self.assertNotEqual(hash_token(ADMIN), ADMIN)
        self.assertEqual(len(hash_token(ADMIN)), 64)

    def test_bad_hash_config_rejected(self):
        with self.assertRaises(AuthConfigError):
            TokenAuthenticator({"admin": "not-a-hash"})
        with self.assertRaises(AuthConfigError):
            TokenAuthenticator({})


class TestViewerRole(unittest.TestCase):
    def test_viewer_token_from_env(self):
        viewer = "viewer-secret-value-789"
        auth = TokenAuthenticator.from_env({"INA_VIEWER_TOKEN_SHA256": hash_token(viewer)})
        self.assertEqual(auth.authenticate(viewer).role, "viewer")
        self.assertIsNone(auth.authenticate(ADMIN))


class TestFromEnv(unittest.TestCase):
    def test_env_hashes(self):
        env = {"INA_ADMIN_TOKEN_SHA256": hash_token(ADMIN),
               "INA_OPERATOR_TOKEN_SHA256": hash_token(OPERATOR)}
        auth = TokenAuthenticator.from_env(env)
        self.assertEqual(auth.authenticate(OPERATOR).role, "operator")
        self.assertFalse(auth.demo)
        self.assertIsNone(auth.authenticate("operator-token"))  # demo tokens off

    def test_nothing_configured_fails_closed(self):
        with self.assertRaises(AuthConfigError):
            TokenAuthenticator.from_env({})

    def test_demo_tokens_need_explicit_flag(self):
        auth = TokenAuthenticator.from_env({"INA_DEMO_TOKENS": "1"})
        self.assertTrue(auth.demo)
        self.assertEqual(auth.authenticate("operator-token").role, "operator")
        self.assertEqual(auth.authenticate("admin-token").role, "admin")
        self.assertEqual(auth.authenticate("viewer-token").role, "viewer")
        with self.assertRaises(AuthConfigError):
            TokenAuthenticator.from_env({"INA_DEMO_TOKENS": "yes"})


if __name__ == "__main__":
    unittest.main()
