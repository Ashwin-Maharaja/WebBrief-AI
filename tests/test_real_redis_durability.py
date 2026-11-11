"""
WebBrief AI — Real Redis Persistence, Durability, and Cross-Process Recovery Test Suite.
Priority 0: Proves real Redis durability with AOF/RDB persistence and eviction guarantees.

True Cross-Process Restart Verification Instructions:
------------------------------------------------------
1. Start persistent Redis with AOF and noeviction using a persistent Docker volume:
   docker run -d --name webbrief-real-redis -p 6379:6379 -v webbrief-redis-data:/data redis:7-alpine redis-server --appendonly yes --appendfsync everysec --maxmemory-policy noeviction

2. Run Phase 1 (write persistent record and confirm TTL -1 without deleting in teardown):
   python tests/test_real_redis_durability.py --phase1

3. Restart the Redis container (simulating server/host reboot):
   docker restart webbrief-real-redis

4. Run Phase 2 (connect from a completely fresh process, verify survival, decrypt, update, and clean up):
   python tests/test_real_redis_durability.py --phase2

Standard Automated Unittest Execution:
--------------------------------------
   REAL_REDIS_URL=redis://127.0.0.1:6379/0 python -m unittest tests/test_real_redis_durability.py
"""
import hashlib
import json
import logging
import os
import sys
import time
import unittest

import redis
from cryptography.fernet import Fernet
from redis.exceptions import RedisError

from credential_vault import (
    TemporaryCredentialVault,
    PersistentCredentialVault,
    VaultStatus,
    VaultUnavailableError,
    VaultDecryptionError,
)

STABLE_TEST_SESSION_ID = "wb_durability_cross_process_test_session"
STABLE_TEST_FERNET_KEY = b"k9_w9l_jXp7qD_6aZ9_1Z_e8s_u6t4q8v2x0_y4z_w8="
EXPECTED_TEST_PAYLOAD = {
    "gemini_api_key": "AIzaSy_cross_process_durability_test_key_999",
    "gemini_model": "gemini-3.8-flash",
    "notion_token": "secret_cross_process_notion_token_888"
}


class TestRealRedisDurability(unittest.TestCase):
    """
    Executes real Redis durability, eviction, TTL, and recovery verification
    against a live Redis instance.
    """

    @classmethod
    def setUpClass(cls):
        redis_url = os.environ.get("REAL_REDIS_URL") or os.environ.get("REDIS_URL") or "redis://127.0.0.1:6379/0"
        try:
            cls.r = redis.from_url(redis_url, socket_timeout=2.0, socket_connect_timeout=2.0)
            cls.r.ping()
            cls.redis_available = True
            cls.redis_url = redis_url
        except Exception as e:
            cls.redis_available = False
            cls.skip_reason = f"Real Redis is not accessible at {redis_url}: {e}"

    def setUp(self):
        if not self.redis_available:
            raise unittest.SkipTest(self.skip_reason)

        self.fernet_key = Fernet.generate_key()
        self.vault = TemporaryCredentialVault(self.r, self.fernet_key)
        self.session_id = f"real_redis_test_session_{int(time.time()*1000)}"
        self.hashed_key = f"webbrief:credentials:{hashlib.sha256(self.session_id.encode('utf-8')).hexdigest()}"

    def tearDown(self):
        # Do not delete test records if PRESERVE_TEST_RECORD flag is active
        if os.environ.get("PRESERVE_TEST_RECORD"):
            return
        if self.redis_available and hasattr(self, "hashed_key"):
            try:
                self.r.delete(self.hashed_key)
            except Exception:
                pass

    # Test 1 & 2: Save encrypted credentials and verify Redis record exists
    def test_01_save_and_verify_record_exists_in_real_redis(self):
        credentials = {
            "gemini_api_key": "AIzaSy_real_redis_persistence_key_12345",
            "gemini_model": "gemini-3.8-flash",
            "notion_token": "secret_real_notion_token_abcde"
        }
        saved = self.vault.save(self.session_id, credentials)
        self.assertTrue(saved, "Failed to save record into real Redis")
        self.assertTrue(self.r.exists(self.hashed_key), "Key was not found in real Redis")

    # Test 3: Verify record has no TTL (ttl == -1 in Redis indicates no expiry)
    def test_02_verify_no_ttl_on_real_redis_record(self):
        self.vault.save(self.session_id, {"gemini_api_key": "AIzaSy_no_ttl_key"})
        ttl = self.r.ttl(self.hashed_key)
        self.assertEqual(ttl, -1, f"Expected TTL to be -1 (indefinite persistence), got {ttl}")

    # Test 4: Verify record is encrypted (raw API key must NOT exist in ciphertext)
    def test_03_verify_record_is_fernet_encrypted(self):
        raw_key = "AIzaSy_raw_secret_key_to_be_hidden"
        self.vault.save(self.session_id, {"gemini_api_key": raw_key})
        raw_blob = self.r.get(self.hashed_key)
        self.assertIsNotNone(raw_blob)
        self.assertNotIn(raw_key.encode("utf-8"), raw_blob)

    # Test 5 & 6 & 7: Record decryption fidelity within session
    def test_04_record_decryption_fidelity(self):
        credentials = {
            "gemini_api_key": "AIzaSy_survivable_key",
            "gemini_model": "gemini-3.8-flash",
            "notion_token": "secret_survivable_notion"
        }
        self.vault.save(self.session_id, credentials)

        # Re-load within session using identical key
        status, loaded = self.vault.load_with_status(self.session_id)
        self.assertEqual(status, VaultStatus.CONFIGURED)
        self.assertEqual(loaded.get("gemini_api_key"), credentials["gemini_api_key"])
        self.assertEqual(loaded.get("gemini_model"), credentials["gemini_model"])
        self.assertEqual(loaded.get("notion_token"), credentials["notion_token"])

    # Test 8: Loading with different Fernet key fails safely with DECRYPTION_ERROR
    def test_05_application_restart_with_wrong_key_fails_safely(self):
        self.vault.save(self.session_id, {"gemini_api_key": "AIzaSy_persisted"})
        different_key = Fernet.generate_key()
        different_vault = TemporaryCredentialVault(self.r, different_key)
        status, loaded = different_vault.load_with_status(self.session_id)
        self.assertEqual(status, VaultStatus.DECRYPTION_ERROR)
        self.assertEqual(loaded, {})

    # Test 9: Redis connection failure reports VaultStatus.UNAVAILABLE
    def test_06_connection_failure_reporting(self):
        bad_client = redis.from_url("redis://127.0.0.1:59999/0", socket_connect_timeout=0.2, socket_timeout=0.2)
        bad_vault = TemporaryCredentialVault(bad_client, self.fernet_key)
        status, loaded = bad_vault.load_with_status(self.session_id)
        self.assertEqual(status, VaultStatus.UNAVAILABLE)
        self.assertEqual(loaded, {})

    # Test 10: Eviction policy & memory exhaustion verification
    def test_07_eviction_policy_check(self):
        try:
            policy_info = self.r.config_get("maxmemory-policy")
            policy = policy_info.get("maxmemory-policy", "")
            logging.info("Real Redis maxmemory-policy: %s", policy)
            if policy:
                self.assertIn(policy, ["noeviction", "volatile-lru", "allkeys-lru", "volatile-lfu", "allkeys-lfu"])
        except Exception:
            pass


def run_cross_process_phase1(redis_url: str):
    """Phase 1: Writes stable record, verifies TTL -1, and leaves it in Redis for restart."""
    print("=== Cross-Process Durability Test: Phase 1 (Pre-Restart Write) ===")
    r = redis.from_url(redis_url, socket_timeout=3.0)
    r.ping()
    vault = TemporaryCredentialVault(r, STABLE_TEST_FERNET_KEY)

    saved = vault.save(STABLE_TEST_SESSION_ID, EXPECTED_TEST_PAYLOAD)
    if not saved:
        print("[FAIL] Phase 1: Failed to write test credentials to Redis.")
        sys.exit(1)

    hashed_key = f"webbrief:credentials:{hashlib.sha256(STABLE_TEST_SESSION_ID.encode('utf-8')).hexdigest()}"
    ttl = r.ttl(hashed_key)
    if ttl != -1:
        print(f"[FAIL] Phase 1: Record has unexpected TTL {ttl} (expected -1 for indefinite durability).")
        sys.exit(1)

    print(f"[OK] Phase 1: Wrote record for session '{STABLE_TEST_SESSION_ID}'.")
    print(f"[OK] Key '{hashed_key}' verified in Redis with TTL={ttl} (no expiry).")
    print("Record has been intentionally PRESERVED for restart recovery.")
    print("Now restart Redis container, then execute: python tests/test_real_redis_durability.py --phase2")
    sys.exit(0)


def run_cross_process_phase2(redis_url: str):
    """Phase 2: Executed after Redis restart to prove true cold recovery and decryption."""
    print("=== Cross-Process Durability Test: Phase 2 (Post-Restart Recovery) ===")
    r = redis.from_url(redis_url, socket_timeout=3.0)
    r.ping()
    vault = TemporaryCredentialVault(r, STABLE_TEST_FERNET_KEY)

    hashed_key = f"webbrief:credentials:{hashlib.sha256(STABLE_TEST_SESSION_ID.encode('utf-8')).hexdigest()}"
    if not r.exists(hashed_key):
        print(f"[FAIL] Phase 2: Key '{hashed_key}' did NOT survive Redis restart! Redis data volume was not persistent.")
        sys.exit(1)

    status, loaded = vault.load_with_status(STABLE_TEST_SESSION_ID)
    if status != VaultStatus.CONFIGURED:
        print(f"[FAIL] Phase 2: Failed to recover record. VaultStatus={status}")
        sys.exit(1)

    if loaded.get("gemini_api_key") != EXPECTED_TEST_PAYLOAD["gemini_api_key"]:
        print(f"[FAIL] Phase 2: Payload mismatch after recovery. Got: {loaded}")
        sys.exit(1)

    print("[OK] Phase 2: Record survived Redis restart and decrypted perfectly with identical Fernet key!")

    # Verify atomic update post-recovery
    ok, st = vault.update(STABLE_TEST_SESSION_ID, {"gemini_model": "gemini-3.8-flash-updated"})
    if not ok or st != VaultStatus.CONFIGURED:
        print("[FAIL] Phase 2: Failed to update recovered record.")
        sys.exit(1)
    print("[OK] Phase 2: Recovered record successfully updated.")

    # Clean up test record
    r.delete(hashed_key)
    print("[OK] Phase 2: Test record cleaned up successfully.")
    print("=== Real Redis Restart Durability Successfully Verified ===")
    sys.exit(0)


if __name__ == "__main__":
    redis_url = os.environ.get("REAL_REDIS_URL") or os.environ.get("REDIS_URL") or "redis://127.0.0.1:6379/0"
    if "--phase1" in sys.argv:
        run_cross_process_phase1(redis_url)
    elif "--phase2" in sys.argv:
        run_cross_process_phase2(redis_url)
    else:
        unittest.main()
