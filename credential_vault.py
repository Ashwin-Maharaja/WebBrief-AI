"""Ephemeral, encrypted BYOK credential storage for Redis-backed Flask sessions."""
import json
import logging
import time
from cryptography.fernet import Fernet, InvalidToken
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class TemporaryCredentialVault:
    TTL_SECONDS = 30 * 60
    MAX_SECONDS = 8 * 60 * 60

    def __init__(self, redis_client, encryption_key):
        self.redis = redis_client
        self.cipher = Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key)

    def _key(self, sid):
        return "webbrief:credentials:" + sid

    def save(self, sid, config) -> bool:
        """Encrypts and stores provider credentials in Redis. Returns True on success, False on failure."""
        record = {"created": int(time.time()), "config": config}
        blob = self.cipher.encrypt(json.dumps(record).encode())
        try:
            self.redis.setex(self._key(sid), self.TTL_SECONDS, blob)
            return True
        except RedisError as e:
            logger.error("Failed to store credentials in Redis vault: %s", e)
            return False

    def load(self, sid) -> dict:
        """Decrypts and returns provider credentials. Gracefully handles expired, invalid, or corrupt entries."""
        key = self._key(sid)
        try:
            blob = self.redis.get(key)
        except RedisError as e:
            logger.error("Failed to fetch credentials from Redis vault: %s", e)
            return {}

        if not blob:
            return {}

        try:
            record = json.loads(self.cipher.decrypt(blob))
            remaining = self.MAX_SECONDS - (int(time.time()) - int(record["created"]))
            if remaining <= 0:
                self.delete(sid)
                return {}
            try:
                self.redis.expire(key, min(self.TTL_SECONDS, remaining))
            except RedisError:
                pass
            return record.get("config", {})
        except (ValueError, KeyError, TypeError, InvalidToken) as e:
            logger.warning("Invalid or unparseable vault record for session: %s", type(e).__name__)
            self.delete(sid)
            return {}

    def delete(self, sid):
        """Removes stored session credentials from Redis."""
        try:
            self.redis.delete(self._key(sid))
        except RedisError as e:
            logger.error("Failed to delete credentials from Redis vault: %s", e)
