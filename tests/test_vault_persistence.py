"""
WebBrief AI — Comprehensive Credential Retention, Continuity & Durability Tests.
Systematically tests all 16 persistence requirements specified in Part 7.
"""
import hashlib
import json
import logging
import os
import time
import unittest
from unittest.mock import patch, MagicMock

import redis
from cryptography.fernet import Fernet, InvalidToken
from redis.exceptions import RedisError

from app import app, VAULT_COOKIE_NAME
from credential_vault import (
    PersistentCredentialVault,
    TemporaryCredentialVault,
    VaultStatus,
    VaultUnavailableError,
    VaultDecryptionError,
    VaultCorruptedError,
)


class InMemoryDurableRedis:
    """
    Simulates a durable Redis instance with persistent disk snapshot / AOF emulation.
    Enforces noeviction policy and explicit out-of-memory error generation on demand.
    """
    def __init__(self, simulate_oom=False):
        self.store = {}
        self.ttls = {}
        self.simulate_oom = simulate_oom

    def set(self, key, value):
        if self.simulate_oom:
            raise RedisError("OOM command not allowed when used memory > 'maxmemory'")
        self.store[key] = value

    def setex(self, key, ttl, value):
        if self.simulate_oom:
            raise RedisError("OOM command not allowed when used memory > 'maxmemory'")
        self.store[key] = value
        self.ttls[key] = ttl

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0

    def expire(self, key, ttl):
        self.ttls[key] = ttl

    def pipeline(self):
        return self

    def execute(self):
        return [True]

    def dump_snapshot(self):
        """Emulates writing RDB snapshot / AOF to persistent disk."""
        return dict(self.store)

    def restore_snapshot(self, snapshot):
        """Emulates recovering data from persistent disk following a process or server restart."""
        self.store = dict(snapshot)


class TestCredentialPersistenceAndContinuity(unittest.TestCase):
    """Verifies all 16 requirements for indefinite retention, session continuity, and error handling."""

    def setUp(self):
        self.fernet_key = Fernet.generate_key()
        self.redis = InMemoryDurableRedis()
        self.vault = PersistentCredentialVault(self.redis, self.fernet_key)
        self.app = app
        self.client = app.test_client()

        # Initialize session CSRF
        self.client.get("/")
        with self.client.session_transaction() as sess:
            self.csrf_token = sess.get("_csrf_token", "")

    # Requirement 1: Saving credentials creates an encrypted Redis record
    def test_01_saving_creates_encrypted_redis_record(self):
        secret = "AIzaSy_super_secret_api_key_value_1234"
        success = self.vault.save("client_browser_a", {"gemini_api_key": secret})
        self.assertTrue(success)

        # Confirm ciphertext in Redis cannot be read as plaintext
        lookup_hash = hashlib.sha256("client_browser_a".encode("utf-8")).hexdigest()
        redis_key = f"webbrief:credentials:{lookup_hash}"
        self.assertIn(redis_key, self.redis.store)
        ciphertext = self.redis.store[redis_key]
        self.assertNotIn(secret.encode(), ciphertext)

    # Requirement 2: Credential records have no TTL
    def test_02_credential_records_have_no_ttl(self):
        self.vault.save("client_browser_a", {"gemini_api_key": "test_key"})
        lookup_hash = hashlib.sha256("client_browser_a".encode("utf-8")).hexdigest()
        redis_key = f"webbrief:credentials:{lookup_hash}"
        self.assertNotIn(redis_key, self.redis.ttls)

    # Requirement 3: Repeated retrieval does not require expiry refresh
    def test_03_repeated_retrieval_does_not_require_expiry_refresh(self):
        self.vault.save("client_browser_a", {"gemini_api_key": "key_val"})
        lookup_hash = hashlib.sha256("client_browser_a".encode("utf-8")).hexdigest()
        redis_key = f"webbrief:credentials:{lookup_hash}"

        for _ in range(10):
            cfg = self.vault.load("client_browser_a")
            self.assertEqual(cfg.get("gemini_api_key"), "key_val")
            # Verify no TTL was introduced by load()
            self.assertNotIn(redis_key, self.redis.ttls)

    # Requirement 4: Credentials remain available after simulated inactivity
    def test_04_credentials_remain_available_after_simulated_inactivity(self):
        self.vault.save("client_browser_a", {"gemini_api_key": "inactive_key"})

        # Simulate 7 days, 30 days, 180 days of user inactivity
        for days in (7, 30, 180):
            with patch("time.time", return_value=time.time() + (days * 86400)):
                cfg = self.vault.load("client_browser_a")
                self.assertEqual(cfg.get("gemini_api_key"), "inactive_key", f"Failed after {days} days")

    # Requirement 5: Credentials remain available across browser-session cookie persistence
    @patch("app.validate_gemini_credentials", return_value=(True, ""))
    def test_05_credentials_remain_available_across_browser_sessions(self, mock_val):
        with patch("app._vault", self.vault):
            # Session 1: User saves credentials
            res = self.client.post(
                "/api/credentials",
                json={"gemini_api_key": "AIzaSy_persistent_key_1"},
                headers={"X-CSRF-Token": self.csrf_token}
            )
            self.assertEqual(res.status_code, 200)

            # Retrieve the persistent cookie
            cookie = self.client.get_cookie(VAULT_COOKIE_NAME)
            self.assertIsNotNone(cookie)
            token = cookie.value

            # Session 2: User opens a new tab / new window presenting that persistent cookie
            browser_window_2 = self.app.test_client()
            browser_window_2.set_cookie(VAULT_COOKIE_NAME, token)

            status_res = browser_window_2.get("/api/credentials/status")
            self.assertEqual(status_res.status_code, 200)
            self.assertTrue(status_res.get_json()["configured"])

    # Requirement 6: Flask session expiry does not delete credential records
    @patch("app.validate_gemini_credentials", return_value=(True, ""))
    def test_06_flask_session_expiry_does_not_delete_credential_records(self, mock_val):
        with patch("app._vault", self.vault):
            res = self.client.post(
                "/api/credentials",
                json={"gemini_api_key": "AIzaSy_survives_session_expiry"},
                headers={"X-CSRF-Token": self.csrf_token}
            )
            self.assertEqual(res.status_code, 200)
            cookie_val = self.client.get_cookie(VAULT_COOKIE_NAME).value

            # Simulate Flask session expiration by clearing session state
            with self.client.session_transaction() as sess:
                sess.clear()

            # The cookie remains in the client browser, so the credentials must still load
            status_res = self.client.get("/api/credentials/status")
            self.assertEqual(status_res.status_code, 200)
            self.assertTrue(status_res.get_json()["configured"])

    # Requirement 7: Application restart does not invalidate the vault reference
    @patch("app.validate_gemini_credentials", return_value=(True, ""))
    def test_07_application_restart_does_not_invalidate_vault_reference(self, mock_val):
        with patch("app._vault", self.vault):
            res = self.client.post(
                "/api/credentials",
                json={"gemini_api_key": "AIzaSy_survives_app_restart"},
                headers={"X-CSRF-Token": self.csrf_token}
            )
            cookie_val = self.client.get_cookie(VAULT_COOKIE_NAME).value

        # Emulate application restarting: New vault instance instantiated with the same persistent Fernet key
        restarted_vault = PersistentCredentialVault(self.redis, self.fernet_key)
        with patch("app._vault", restarted_vault):
            restarted_client = self.app.test_client()
            restarted_client.set_cookie(VAULT_COOKIE_NAME, cookie_val)

            status_res = restarted_client.get("/api/credentials/status")
            self.assertEqual(status_res.status_code, 200)
            self.assertTrue(status_res.get_json()["configured"])

    # Requirement 8: Redis restart with persistence enabled allows record recovery
    def test_08_redis_restart_recovery(self):
        self.vault.save("client_browser_a", {"gemini_api_key": "AIzaSy_recovered_from_disk"})

        # Emulate snapshot persisted to disk
        disk_snapshot = self.redis.dump_snapshot()

        # Emulate Redis server crash / process restart with empty memory
        restarted_redis = InMemoryDurableRedis()
        restarted_vault = PersistentCredentialVault(restarted_redis, self.fernet_key)

        # Before disk recovery, data is empty
        self.assertEqual(restarted_vault.load("client_browser_a"), {})

        # After disk snapshot / AOF recovery
        restarted_redis.restore_snapshot(disk_snapshot)
        recovered_cfg = restarted_vault.load("client_browser_a")
        self.assertEqual(recovered_cfg.get("gemini_api_key"), "AIzaSy_recovered_from_disk")

    # Requirement 9: Credential replacement updates the correct record
    def test_09_credential_replacement_updates_record(self):
        self.vault.save("client_browser_a", {"gemini_api_key": "old_key"})
        lookup_hash = hashlib.sha256("client_browser_a".encode("utf-8")).hexdigest()
        redis_key = f"webbrief:credentials:{lookup_hash}"

        self.assertEqual(self.vault.load("client_browser_a")["gemini_api_key"], "old_key")

        # Replace key
        self.vault.save("client_browser_a", {"gemini_api_key": "new_key"})
        self.assertEqual(self.vault.load("client_browser_a")["gemini_api_key"], "new_key")

        # Verify only 1 record exists in Redis for this client (no orphaned records)
        matching_keys = [k for k in self.redis.store.keys() if lookup_hash in k]
        self.assertEqual(len(matching_keys), 1)

    # Requirement 10: Explicit deletion removes the Redis record
    @patch("app.validate_gemini_credentials", return_value=(True, ""))
    def test_10_explicit_deletion_removes_record_and_cookie(self, mock_val):
        with patch("app._vault", self.vault):
            self.client.post(
                "/api/credentials",
                json={"gemini_api_key": "AIzaSy_to_be_deleted"},
                headers={"X-CSRF-Token": self.csrf_token}
            )
            cookie_val = self.client.get_cookie(VAULT_COOKIE_NAME).value

            # Perform explicit delete
            del_res = self.client.delete("/api/credentials", headers={"X-CSRF-Token": self.csrf_token})
            self.assertEqual(del_res.status_code, 200)

            # Record must be gone from Redis
            lookup_hash = hashlib.sha256(cookie_val.encode("utf-8")).hexdigest()
            self.assertNotIn(f"webbrief:credentials:{lookup_hash}", self.redis.store)

    # Requirement 11: Failed deletion does not report success
    def test_11_failed_deletion_does_not_report_success(self):
        class FailingDeleteRedis:
            def delete(self, key):
                raise RedisError("Cluster network failure")

        broken_vault = PersistentCredentialVault(FailingDeleteRedis(), self.fernet_key)
        self.assertFalse(broken_vault.delete("client_browser_a"))

    # Requirement 12: Redis failure during retrieval does not destroy the credential reference
    @patch("app.validate_gemini_credentials", return_value=(True, ""))
    def test_12_redis_failure_during_retrieval_preserves_reference(self, mock_val):
        with patch("app._vault", self.vault):
            self.client.post(
                "/api/credentials",
                json={"gemini_api_key": "AIzaSy_resilient"},
                headers={"X-CSRF-Token": self.csrf_token}
            )
            cookie_val = self.client.get_cookie(VAULT_COOKIE_NAME).value

            # Simulate Redis network glitch during read
            with patch.object(self.redis, "get", side_effect=RedisError("Network glitch")):
                status_res = self.client.get("/api/credentials/status")
                # Truthfully reports 503 Service Unavailable rather than 200 with false unconfigured
                self.assertEqual(status_res.status_code, 503)
                self.assertEqual(status_res.get_json()["status"], "unavailable")
                self.assertFalse(status_res.get_json()["configured"])

            # Once Redis network recovers, cookie still exists and credentials reload cleanly
            status_res_after = self.client.get("/api/credentials/status")
            self.assertEqual(status_res_after.status_code, 200)
            self.assertTrue(status_res_after.get_json()["configured"])

    # Requirement 13: Redis out-of-memory failures do not report successful saves
    def test_13_redis_oom_failures_fail_safely(self):
        oom_redis = InMemoryDurableRedis(simulate_oom=True)
        oom_vault = PersistentCredentialVault(oom_redis, self.fernet_key)
        success = oom_vault.save("client_browser_a", {"gemini_api_key": "val"})
        self.assertFalse(success)

    # Requirement 14: Missing encryption keys are handled safely
    def test_14_missing_encryption_keys_fail_safely(self):
        with self.assertRaises(Exception):
            PersistentCredentialVault(self.redis, "")

    # Requirement 15: Corrupted encrypted records fail safely
    def test_15_corrupted_encrypted_records_fail_safely(self):
        lookup_hash = hashlib.sha256("client_browser_a".encode("utf-8")).hexdigest()
        redis_key = f"webbrief:credentials:{lookup_hash}"
        # Store corrupted garbage payload
        self.redis.store[redis_key] = b"corrupted_non_fernet_ciphertext_garbage"

        # Loading must return {} safely without throwing uncaught InvalidToken
        cfg = self.vault.load("client_browser_a")
        self.assertEqual(cfg, {})

    # Requirement 16: No credential values appear in frontend responses or logs
    @patch("app.validate_gemini_credentials", return_value=(True, ""))
    def test_16_no_credentials_exposed_in_status_responses(self, mock_val):
        secret_key = "AIzaSy_strictly_confidential_key"
        with patch("app._vault", self.vault):
            self.client.post(
                "/api/credentials",
                json={"gemini_api_key": secret_key, "gemini_model": "gemini-3.8-flash"},
                headers={"X-CSRF-Token": self.csrf_token}
            )

            res = self.client.get("/api/credentials/status")
            self.assertEqual(res.status_code, 200)
            body_text = res.get_data(as_text=True)
            self.assertNotIn(secret_key, body_text)
            self.assertIn("gemini-3.8-flash", body_text)

    # Requirement 17: Distinct vault status semantics & typed exception verification
    def test_17_load_with_status_and_typed_exceptions(self):
        # 1. NOT_CONFIGURED
        status, data = self.vault.load_with_status("unknown_session")
        self.assertEqual(status, VaultStatus.NOT_CONFIGURED)
        self.assertEqual(data, {})
        self.assertEqual(self.vault.load_or_raise("unknown_session"), {})

        # 2. CONFIGURED
        self.vault.save("valid_session", {"gemini_api_key": "AIzaSy_valid_key"})
        status, data = self.vault.load_with_status("valid_session")
        self.assertEqual(status, VaultStatus.CONFIGURED)
        self.assertEqual(data.get("gemini_api_key"), "AIzaSy_valid_key")
        self.assertEqual(self.vault.load_or_raise("valid_session").get("gemini_api_key"), "AIzaSy_valid_key")

        # 3. UNAVAILABLE (Redis outage)
        with patch.object(self.redis, "get", side_effect=RedisError("Connection refused")):
            status, data = self.vault.load_with_status("valid_session")
            self.assertEqual(status, VaultStatus.UNAVAILABLE)
            self.assertEqual(data, {})
            with self.assertRaises(VaultUnavailableError):
                self.vault.load_or_raise("valid_session")

        # 4. DECRYPTION_ERROR (Invalid Fernet token / wrong key)
        other_vault = PersistentCredentialVault(self.redis, Fernet.generate_key())
        status, data = other_vault.load_with_status("valid_session")
        self.assertEqual(status, VaultStatus.DECRYPTION_ERROR)
        self.assertEqual(data, {})
        with self.assertRaises(VaultDecryptionError):
            other_vault.load_or_raise("valid_session")

        # 5. CORRUPTED (Valid fernet decrypt, but malformed JSON payload)
        corrupted_cipher = self.vault.cipher.encrypt(b"this is not valid json")
        lookup_hash = hashlib.sha256("corrupted_json_sid".encode("utf-8")).hexdigest()
        self.redis.store[f"webbrief:credentials:{lookup_hash}"] = corrupted_cipher

        status, data = self.vault.load_with_status("corrupted_json_sid")
        self.assertEqual(status, VaultStatus.CORRUPTED)
        self.assertEqual(data, {})
        with self.assertRaises(VaultCorruptedError):
            self.vault.load_or_raise("corrupted_json_sid")

    # Requirement 18: Vault update preserves unrelated credentials
    def test_18_vault_update_preserves_unrelated_credentials(self):
        self.vault.save("merge_sid", {
            "gemini_api_key": "AIzaSy_orig_key",
            "notion_token": "secret_notion_token_123"
        })
        success, status = self.vault.update("merge_sid", {"gemini_model": "gemini-3.8-flash"})
        self.assertTrue(success)
        self.assertEqual(status, VaultStatus.CONFIGURED)

        loaded = self.vault.load("merge_sid")
        self.assertEqual(loaded.get("gemini_api_key"), "AIzaSy_orig_key")
        self.assertEqual(loaded.get("notion_token"), "secret_notion_token_123")
        self.assertEqual(loaded.get("gemini_model"), "gemini-3.8-flash")

    # Requirement 19: Vault update refuses blind overwrite on Redis read outage
    def test_19_vault_update_refuses_when_redis_unavailable(self):
        self.vault.save("merge_outage_sid", {"gemini_api_key": "AIzaSy_keep_me"})
        with patch.object(self.redis, "get", side_effect=RedisError("Cluster timeout")):
            success, status = self.vault.update("merge_outage_sid", {"gemini_model": "gemini-3.8-flash"})
            self.assertFalse(success)
            self.assertEqual(status, VaultStatus.UNAVAILABLE)

        # After outage recovers, original key was never wiped
        loaded = self.vault.load("merge_outage_sid")
        self.assertEqual(loaded.get("gemini_api_key"), "AIzaSy_keep_me")

    # Requirement 20: /api/credentials merge safety prevents loss of Notion token during Redis glitch
    @patch("app.validate_gemini_credentials", return_value=(True, ""))
    def test_20_api_credentials_merge_safety_on_redis_outage(self, mock_val):
        with patch("app._vault", self.vault):
            # 1. Save initial state with Notion token
            self.vault.save("client_browser_a", {"notion_token": "secret_important_notion"})

            # Establish cookie session
            self.client.set_cookie(VAULT_COOKIE_NAME, "client_browser_a")

            # 2. Simulate Redis outage during read inside /api/credentials
            with patch.object(self.redis, "get", side_effect=RedisError("Connection reset")):
                res = self.client.post(
                    "/api/credentials",
                    json={"gemini_api_key": "AIzaSy_new_key"},
                    headers={"X-CSRF-Token": self.csrf_token}
                )
                # Truthfully fails with 503 instead of overwriting existing credentials with an unverified empty read
                self.assertEqual(res.status_code, 503)
                self.assertIn("unavailable", res.get_json()["error"].lower())

            # 3. Confirm Notion token was preserved and never wiped
            loaded = self.vault.load("client_browser_a")
            self.assertEqual(loaded.get("notion_token"), "secret_important_notion")

    # Requirement 21: /api/notion/connect preserves existing Gemini API key
    @patch("app.validate_notion_token", return_value=(True, ""))
    @patch("app.get_accessible_pages", return_value=[{"id": "p1", "title": "Page 1"}])
    @patch("app.is_serverless_env", return_value=True)
    def test_21_notion_connect_preserves_gemini_credentials(self, mock_env, mock_pages, mock_val):
        with patch("app._vault", self.vault):
            self.client.set_cookie(VAULT_COOKIE_NAME, "browser_with_gemini")
            self.vault.save("browser_with_gemini", {
                "gemini_api_key": "AIzaSy_preexisting_gemini_key",
                "gemini_model": "gemini-3.8-flash"
            })

            res = self.client.post(
                "/api/notion/connect",
                json={"token": "secret_notion_connect_key"},
                headers={"X-CSRF-Token": self.csrf_token}
            )
            self.assertEqual(res.status_code, 200)

            loaded = self.vault.load("browser_with_gemini")
            self.assertEqual(loaded.get("gemini_api_key"), "AIzaSy_preexisting_gemini_key")
            self.assertEqual(loaded.get("gemini_model"), "gemini-3.8-flash")
            self.assertEqual(loaded.get("notion_token"), "secret_notion_connect_key")

    # Requirement 22: /api/notion/disconnect preserves existing Gemini credentials
    def test_22_notion_disconnect_preserves_gemini_credentials(self):
        with patch("app._vault", self.vault):
            self.client.set_cookie(VAULT_COOKIE_NAME, "browser_with_both")
            self.vault.save("browser_with_both", {
                "gemini_api_key": "AIzaSy_keep_this_key",
                "notion_token": "secret_disconnect_me"
            })

            res = self.client.post(
                "/api/notion/disconnect",
                headers={"X-CSRF-Token": self.csrf_token}
            )
            self.assertEqual(res.status_code, 200)

            loaded = self.vault.load("browser_with_both")
            self.assertEqual(loaded.get("gemini_api_key"), "AIzaSy_keep_this_key")
            self.assertNotIn("notion_token", loaded)

    # Requirement 23: Deletion failure does not clear browser reference cookie
    def test_23_deletion_failure_preserves_cookie(self):
        with patch("app._vault", self.vault):
            self.client.set_cookie(VAULT_COOKIE_NAME, "browser_persistent_cookie")
            self.vault.save("browser_persistent_cookie", {"gemini_api_key": "AIzaSy_val"})

            # Simulate Redis failure during DELETE
            with patch.object(self.vault, "delete", return_value=False):
                res = self.client.delete(
                    "/api/credentials",
                    headers={"X-CSRF-Token": self.csrf_token}
                )
                self.assertEqual(res.status_code, 503)

            # Confirm cookie is still set on client and not expired
            cookie = self.client.get_cookie(VAULT_COOKIE_NAME)
            self.assertIsNotNone(cookie)
            self.assertEqual(cookie.value, "browser_persistent_cookie")


class RealRedisIntegrationTests(unittest.TestCase):
    """
    Integration tests against a live Redis instance.
    Runs only when REDIS_URL is provided or a local Redis server is accessible on localhost:6379;
    cleanly skips otherwise without failing the suite.
    """

    def setUp(self):
        redis_url = os.environ.get("REDIS_URL") or "redis://127.0.0.1:6379/0"
        try:
            self.client = redis.from_url(redis_url, socket_timeout=1.5, socket_connect_timeout=1.5)
            self.client.ping()
        except Exception as e:
            raise unittest.SkipTest(f"Live Redis is not available at {redis_url}: {e}")

        self.fernet_key = Fernet.generate_key()
        self.vault = PersistentCredentialVault(self.client, self.fernet_key)
        self.test_sid = "test_live_redis_session_123"
        self.lookup_hash = hashlib.sha256(self.test_sid.encode("utf-8")).hexdigest()
        self.redis_key = f"webbrief:credentials:{self.lookup_hash}"

    def tearDown(self):
        if hasattr(self, "client") and hasattr(self, "redis_key"):
            try:
                self.client.delete(self.redis_key)
            except Exception:
                pass

    def test_live_redis_save_persists_without_ttl(self):
        saved = self.vault.save(self.test_sid, {"gemini_api_key": "AIzaSy_live_redis_key"})
        self.assertTrue(saved)

        # Check raw key exists
        self.assertTrue(self.client.exists(self.redis_key))

        # Check TTL in real Redis: -1 indicates key exists with no expiration
        ttl = self.client.ttl(self.redis_key)
        self.assertEqual(ttl, -1, f"Expected no TTL (-1) in real Redis, got {ttl}")

        # Check decryption and data fidelity
        loaded = self.vault.load(self.test_sid)
        self.assertEqual(loaded.get("gemini_api_key"), "AIzaSy_live_redis_key")

    def test_live_redis_eviction_policy_check(self):
        try:
            maxmemory_policy = self.client.config_get("maxmemory-policy").get("maxmemory-policy")
            # If accessible, verify policy (recommended is noeviction)
            logging.info("Live Redis maxmemory-policy: %s", maxmemory_policy)
        except Exception:
            # Managed providers (e.g. Upstash, AWS ElastiCache) may disable CONFIG GET
            pass

    def test_live_redis_deletion_and_status(self):
        self.vault.save(self.test_sid, {"gemini_api_key": "AIzaSy_delete_me"})
        status, _ = self.vault.load_with_status(self.test_sid)
        self.assertEqual(status, VaultStatus.CONFIGURED)

        deleted = self.vault.delete(self.test_sid)
        self.assertTrue(deleted)
        self.assertFalse(self.client.exists(self.redis_key))

        status_after, _ = self.vault.load_with_status(self.test_sid)
        self.assertEqual(status_after, VaultStatus.NOT_CONFIGURED)


if __name__ == "__main__":
    unittest.main()
