"""No network required: encrypted vault lifecycle and Flask endpoint tests."""
import hashlib
import json
import time
import unittest
from unittest.mock import patch, MagicMock
from cryptography.fernet import Fernet
from redis.exceptions import RedisError
from credential_vault import TemporaryCredentialVault, PersistentCredentialVault
from app import app, VAULT_COOKIE_NAME


class FakePipeline:
    def __init__(self, fake_redis):
        self.fake_redis = fake_redis
        self.actions = []

    def watch(self, *keys):
        return self

    def unwatch(self):
        return self

    def get(self, key):
        return self.fake_redis.get(key)

    def set(self, key, value):
        self.actions.append(lambda: self.fake_redis.set(key, value))
        return self

    def multi(self):
        return self

    def delete(self, key):
        self.actions.append(lambda: self.fake_redis.delete(key))
        return self

    def execute(self):
        results = []
        for act in self.actions:
            results.append(act())
        self.actions = []
        return results


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.ttls = {}

    def set(self, key, value):
        self.data[key] = value

    def setex(self, key, ttl, value):
        self.data[key] = value
        self.ttls[key] = ttl

    def get(self, key):
        return self.data.get(key)

    def expire(self, key, ttl):
        self.ttls[key] = ttl

    def delete(self, key):
        self.data.pop(key, None)
        return 1

    def pipeline(self):
        return FakePipeline(self)


class BrokenRedis:
    """Simulates a Redis instance throwing network or connection exceptions."""
    def set(self, key, value):
        raise RedisError("Redis connection refused")

    def setex(self, key, ttl, value):
        raise RedisError("Redis connection refused")

    def get(self, key):
        raise RedisError("Redis read timeout")

    def expire(self, key, ttl):
        raise RedisError("Redis socket timeout")

    def delete(self, key):
        raise RedisError("Redis cluster unreachable")

    def pipeline(self):
        raise RedisError("Redis connection refused")


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.key = Fernet.generate_key()
        self.vault = PersistentCredentialVault(self.redis, self.key)

    def test_encryption_and_session_isolation(self):
        saved = self.vault.save('alice', {'primary_provider': 'gemini', 'gemini_api_key': 'AIzaSy_test_secret'})
        self.assertTrue(saved)
        # Check that lookup key uses SHA-256 hash of 'alice'
        expected_hash = hashlib.sha256('alice'.encode('utf-8')).hexdigest()
        expected_key = f"webbrief:credentials:{expected_hash}"
        self.assertIn(expected_key, self.redis.data)

        # Raw secret is encrypted in cipher blob
        blob = self.redis.data[expected_key]
        self.assertNotIn(b'AIzaSy_test_secret', blob)
        self.assertEqual(self.vault.load('alice')['gemini_api_key'], 'AIzaSy_test_secret')
        self.assertEqual(self.vault.load('bob'), {})

    def test_explicit_delete(self):
        self.vault.save('alice', {'gemini_api_key': 'test'})
        self.vault.delete('alice')
        self.assertEqual(self.vault.load('alice'), {})

    def test_indefinite_retention_across_time(self):
        """Verifies that credentials do NOT expire after 30 minutes, 8 hours, or 1 year."""
        self.vault.save('alice', {'gemini_api_key': 'test_indefinite'})
        # Verify no TTL was set in Redis
        expected_hash = hashlib.sha256('alice'.encode('utf-8')).hexdigest()
        self.assertNotIn(f"webbrief:credentials:{expected_hash}", self.redis.ttls)

        # Simulate 1 year in the future
        with patch('time.time', return_value=time.time() + 365 * 86400):
            loaded = self.vault.load('alice')
            self.assertEqual(loaded.get('gemini_api_key'), 'test_indefinite')

    def test_invalid_token_handling(self):
        """Verifies that key mismatch gracefully returns {} without raising uncaught InvalidToken."""
        self.vault.save('alice', {'gemini_api_key': 'secret_value'})
        # Create a second vault instance with a different key
        other_vault = PersistentCredentialVault(self.redis, Fernet.generate_key())
        loaded = other_vault.load('alice')
        self.assertEqual(loaded, {})

    def test_redis_error_graceful_handling(self):
        """Verifies that Redis network or timeout errors do not crash callers."""
        broken_vault = PersistentCredentialVault(BrokenRedis(), self.key)
        self.assertFalse(broken_vault.save('alice', {'gemini_api_key': 'secret'}))
        self.assertEqual(broken_vault.load('alice'), {})
        self.assertFalse(broken_vault.delete('alice'))

    def test_legacy_key_migration(self):
        """Verifies that legacy unhashed Redis keys can still be loaded and migrated."""
        legacy_key = "webbrief:credentials:legacy_alice"
        record = {"_created": int(time.time()), "config": {"gemini_api_key": "legacy_val"}}
        blob = self.vault.cipher.encrypt(json.dumps(record).encode('utf-8'))
        self.redis.data[legacy_key] = blob

        # Should load via legacy fallback
        loaded = self.vault.load("legacy_alice")
        self.assertEqual(loaded.get("gemini_api_key"), "legacy_val")

        # Saving should migrate to hashed key and clean up legacy key
        self.vault.save("legacy_alice", {"gemini_api_key": "migrated_val"})
        hashed_key = self.vault._key("legacy_alice")
        self.assertIn(hashed_key, self.redis.data)
        self.assertNotIn(legacy_key, self.redis.data)


class TestAppVaultRoutes(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        # Initialize session with CSRF token
        self.client.get('/')
        with self.client.session_transaction() as sess:
            self.csrf_token = sess.get('_csrf_token', '')

    def test_vault_unconfigured_status(self):
        res = self.client.get('/api/credentials/status')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertFalse(data['enabled'])
        self.assertFalse(data['configured'])

    def test_vault_unconfigured_post(self):
        res = self.client.post(
            '/api/credentials',
            json={'primary_provider': 'gemini', 'gemini_api_key': 'AIzaSy_test'},
            headers={'X-CSRF-Token': self.csrf_token}
        )
        self.assertEqual(res.status_code, 503)

    @patch('app.validate_gemini_credentials', return_value=(True, ''))
    def test_vault_configured_lifecycle(self, mock_val):
        fake_redis = FakeRedis()
        test_vault = PersistentCredentialVault(fake_redis, Fernet.generate_key())
        with patch('app._vault', test_vault):
            # Post credentials
            res = self.client.post(
                '/api/credentials',
                json={
                    'primary_provider': 'gemini',
                    'gemini_api_key': 'AIzaSy_test_key_12345',
                    'gemini_model': 'gemini-3.8-flash'
                },
                headers={'X-CSRF-Token': self.csrf_token}
            )
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.get_json()['success'])
            self.assertTrue(res.get_json().get('persistent', False))

            # Verify persistent cookie was set
            cookie_headers = [h[1] for h in res.headers if h[0].lower() == 'set-cookie']
            self.assertTrue(any(VAULT_COOKIE_NAME in ch for ch in cookie_headers))

            # Status check
            res_status = self.client.get('/api/credentials/status')
            self.assertEqual(res_status.status_code, 200)
            status_data = res_status.get_json()
            self.assertTrue(status_data['enabled'])
            self.assertTrue(status_data['configured'])
            self.assertEqual(status_data['provider'], 'gemini')
            # Ensure key itself is NOT in status response
            self.assertNotIn('AIzaSy_test_key_12345', str(status_data))

            # Delete credentials
            res_del = self.client.delete('/api/credentials', headers={'X-CSRF-Token': self.csrf_token})
            self.assertEqual(res_del.status_code, 200)

            # Status check after delete
            res_status2 = self.client.get('/api/credentials/status')
            self.assertFalse(res_status2.get_json()['configured'])

    @patch('app.validate_gemini_credentials', return_value=(True, ''))
    def test_cross_session_cookie_continuity(self, mock_val):
        """Verifies that an opaque browser cookie retains access even if Flask session expires."""
        fake_redis = FakeRedis()
        test_vault = PersistentCredentialVault(fake_redis, Fernet.generate_key())
        with patch('app._vault', test_vault):
            # 1. Save credentials in Session 1
            res = self.client.post(
                '/api/credentials',
                json={'primary_provider': 'gemini', 'gemini_api_key': 'AIzaSy_persisted_key'},
                headers={'X-CSRF-Token': self.csrf_token}
            )
            self.assertEqual(res.status_code, 200)

            # Extract vault cookie
            vault_cookie = self.client.get_cookie(VAULT_COOKIE_NAME)
            self.assertIsNotNone(vault_cookie)
            vault_id = vault_cookie.value

            # 2. Simulate browser restart / session expiration with a NEW test client having ONLY the vault cookie
            new_client = app.test_client()
            new_client.set_cookie(VAULT_COOKIE_NAME, vault_id)

            # Check status on new client
            res2 = new_client.get('/api/credentials/status')
            self.assertEqual(res2.status_code, 200)
            self.assertTrue(res2.get_json()['configured'])

    @patch('app.validate_gemini_credentials', return_value=(True, ''))
    def test_vault_storage_failure_returns_503(self, mock_val):
        broken_vault = PersistentCredentialVault(BrokenRedis(), Fernet.generate_key())
        with patch('app._vault', broken_vault):
            res = self.client.post(
                '/api/credentials',
                json={'primary_provider': 'gemini', 'gemini_api_key': 'AIzaSy_valid_key'},
                headers={'X-CSRF-Token': self.csrf_token}
            )
            self.assertEqual(res.status_code, 503)

    @patch('app.validate_gemini_credentials', return_value=(True, ''))
    def test_save_config_vault_storage_failure_returns_503(self, mock_val):
        """Verifies that /api/save-config does not return success when vault write fails in serverless mode."""
        broken_vault = PersistentCredentialVault(BrokenRedis(), Fernet.generate_key())
        with patch('app._vault', broken_vault):
            with patch('app.is_serverless_env', return_value=True):
                res = self.client.post(
                    '/api/save-config',
                    json={'primary_provider': 'gemini', 'gemini_api_key': 'AIzaSy_valid_key'},
                    headers={'X-CSRF-Token': self.csrf_token}
                )
                self.assertEqual(res.status_code, 503)
                data = res.get_json()
                self.assertFalse(data.get('success', False))
                self.assertTrue(
                    "Failed to store" in data.get('error', '') or "unavailable" in data.get('error', '').lower()
                )

    @patch('app.validate_gemini_credentials', return_value=(False, 'Invalid Gemini API key'))
    def test_vault_invalid_credentials_rejection(self, mock_gemini):
        """Verifies that invalid credentials are authenticated and rejected before storage."""
        fake_redis = FakeRedis()
        test_vault = PersistentCredentialVault(fake_redis, Fernet.generate_key())
        with patch('app._vault', test_vault):
            res = self.client.post(
                '/api/credentials',
                json={
                    'primary_provider': 'gemini',
                    'gemini_api_key': 'bad_key',
                },
                headers={'X-CSRF-Token': self.csrf_token}
            )
            self.assertEqual(res.status_code, 400)
            self.assertIn("Provider credentials failed validation", res.get_json().get("error", ""))


if __name__ == '__main__':
    unittest.main()
