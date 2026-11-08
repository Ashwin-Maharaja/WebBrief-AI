"""Ephemeral, encrypted BYOK credential storage for Redis-backed Flask sessions."""
import json
import time
from cryptography.fernet import Fernet


class TemporaryCredentialVault:
    TTL_SECONDS = 30 * 60
    MAX_SECONDS = 8 * 60 * 60

    def __init__(self, redis_client, encryption_key):
        self.redis = redis_client
        self.cipher = Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key)

    def _key(self, sid):
        return "webbrief:credentials:" + sid

    def save(self, sid, config):
        record = {"created": int(time.time()), "config": config}
        blob = self.cipher.encrypt(json.dumps(record).encode())
        self.redis.setex(self._key(sid), self.TTL_SECONDS, blob)

    def load(self, sid):
        key = self._key(sid)
        blob = self.redis.get(key)
        if not blob:
            return {}
        try:
            record = json.loads(self.cipher.decrypt(blob))
            remaining = self.MAX_SECONDS - (int(time.time()) - int(record["created"]))
            if remaining <= 0:
                self.redis.delete(key)
                return {}
            self.redis.expire(key, min(self.TTL_SECONDS, remaining))
            return record["config"]
        except (ValueError, KeyError, TypeError):
            self.redis.delete(key)
            return {}

    def delete(self, sid):
        self.redis.delete(self._key(sid))
