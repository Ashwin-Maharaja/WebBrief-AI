"""No network required: encrypted vault lifecycle and Flask endpoint tests."""
import json
import time
import unittest
from unittest.mock import patch, MagicMock
from cryptography.fernet import Fernet
from redis.exceptions import RedisError
from credential_vault import TemporaryCredentialVault
from app import app


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.ttls = {}

    def setex(self, key, ttl, value):
        self.data[key] = value
        self.ttls[key] = ttl

    def get(self, key):
        return self.data.get(key)

    def expire(self, key, ttl):
        self.ttls[key] = ttl

    def delete(self, key):
        self.data.pop(key, None)


class BrokenRedis:
    """Simulates a Redis instance throwing network or connection exceptions."""
    def setex(self, key, ttl, value):
        raise RedisError("Redis connection refused")

    def get(self, key):
        raise RedisError("Redis read timeout")

    def expire(self, key, ttl):
        raise RedisError("Redis socket timeout")

    def delete(self, key):
        raise RedisError("Redis cluster unreachable")


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.key = Fernet.generate_key()
        self.vault = TemporaryCredentialVault(self.redis, self.key)

    def test_encryption_and_session_isolation(self):
        saved = self.vault.save('alice', {'primary_provider': 'groq', 'groq_api_key': 'gsk_test_secret'})
        self.assertTrue(saved)
        blob = next(iter(self.redis.data.values()))
        self.assertNotIn(b'gsk_test_secret', blob)
        self.assertEqual(self.vault.load('alice')['groq_api_key'], 'gsk_test_secret')
        self.assertEqual(self.vault.load('bob'), {})

    def test_explicit_delete(self):
        self.vault.save('alice', {'groq_api_key': 'test'})
        self.vault.delete('alice')
        self.assertEqual(self.vault.load('alice'), {})

    def test_absolute_expiration(self):
        self.vault.save('alice', {'groq_api_key': 'test'})
        with patch('credential_vault.time.time', return_value=time.time() + 9 * 3600):
            self.assertEqual(self.vault.load('alice'), {})

    def test_invalid_token_handling(self):
        """Verifies that key rotation or corrupted cipher blobs do NOT raise uncaught InvalidToken."""
        self.vault.save('alice', {'groq_api_key': 'secret_value'})
        # Create a second vault instance with a different key
        other_vault = TemporaryCredentialVault(self.redis, Fernet.generate_key())
        # Should gracefully return {} and delete corrupted record without crashing
        loaded = other_vault.load('alice')
        self.assertEqual(loaded, {})
        # Verify the unparseable key was removed from Redis
        self.assertIsNone(self.redis.get(self.vault._key('alice')))

    def test_redis_error_graceful_handling(self):
        """Verifies that Redis network or timeout errors do not crash callers."""
        broken_vault = TemporaryCredentialVault(BrokenRedis(), self.key)
        self.assertFalse(broken_vault.save('alice', {'groq_api_key': 'secret'}))
        self.assertEqual(broken_vault.load('alice'), {})
        # delete should not raise
        broken_vault.delete('alice')


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
            json={'primary_provider': 'groq', 'groq_api_key': 'gsk_test'},
            headers={'X-CSRF-Token': self.csrf_token}
        )
        self.assertEqual(res.status_code, 503)

    @patch('app.validate_groq_credentials', return_value=(True, ''))
    def test_vault_configured_lifecycle(self, mock_val):
        fake_redis = FakeRedis()
        test_vault = TemporaryCredentialVault(fake_redis, Fernet.generate_key())
        with patch('app._vault', test_vault):
            # Post credentials
            res = self.client.post(
                '/api/credentials',
                json={
                    'primary_provider': 'groq',
                    'groq_api_key': 'gsk_test_key_12345',
                    'fallback_provider': 'cloudflare',
                    'primary_model': 'llama-3.3-70b-versatile'
                },
                headers={'X-CSRF-Token': self.csrf_token}
            )
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.get_json()['success'])

            # Status check
            res_status = self.client.get('/api/credentials/status')
            self.assertEqual(res_status.status_code, 200)
            status_data = res_status.get_json()
            self.assertTrue(status_data['enabled'])
            self.assertTrue(status_data['configured'])
            self.assertEqual(status_data['provider'], 'groq')
            # Ensure key itself is NOT in status response
            self.assertNotIn('gsk_test_key_12345', str(status_data))

            # Delete credentials
            res_del = self.client.delete('/api/credentials', headers={'X-CSRF-Token': self.csrf_token})
            self.assertEqual(res_del.status_code, 200)

            # Status check after delete
            res_status2 = self.client.get('/api/credentials/status')
            self.assertFalse(res_status2.get_json()['configured'])

    @patch('app.validate_groq_credentials', return_value=(True, ''))
    def test_vault_storage_failure_returns_503(self, mock_val):
        broken_vault = TemporaryCredentialVault(BrokenRedis(), Fernet.generate_key())
        with patch('app._vault', broken_vault):
            res = self.client.post(
                '/api/credentials',
                json={'primary_provider': 'groq', 'groq_api_key': 'gsk_valid_key'},
                headers={'X-CSRF-Token': self.csrf_token}
            )
            self.assertEqual(res.status_code, 503)


if __name__ == '__main__':
    unittest.main()
