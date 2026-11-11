"""Encrypted BYOK credential storage for Redis-backed persistent sessions."""
import hashlib
import json
import logging
import time
from enum import Enum
from typing import Dict, Any, Optional, Tuple
from cryptography.fernet import Fernet, InvalidToken
from redis.exceptions import RedisError
try:
    from redis.exceptions import WatchError
except ImportError:
    class WatchError(Exception):
        pass

logger = logging.getLogger(__name__)


class VaultStatus(str, Enum):
    """Explicit outcomes for credential retrieval."""
    CONFIGURED = "configured"            # Record exists and decrypted successfully
    NOT_CONFIGURED = "not_configured"    # Genuinely no record exists for this session
    UNAVAILABLE = "unavailable"          # Redis connection, socket, or timeout error
    DECRYPTION_ERROR = "decryption_error"# Fernet key invalid or mismatched
    CORRUPTED = "corrupted"              # Malformed JSON or unparseable record payload


class VaultError(Exception):
    """Base exception for credential vault operations."""
    pass


class VaultUnavailableError(VaultError):
    """Raised when Redis storage backend is unreachable, timed out, or connection refused."""
    pass


class VaultDecryptionError(VaultError):
    """Raised when stored credentials cannot be decrypted with the active Fernet key."""
    pass


class VaultCorruptedError(VaultError):
    """Raised when stored credential record payload is malformed or unparseable."""
    pass


class TemporaryCredentialVault:
    """
    Encrypted credential vault using Redis and server-side Fernet encryption.
    Credentials persist indefinitely across browser sessions and application restarts
    until explicitly replaced or deleted by the user.
    Uses SHA-256 hashed lookup keys to prevent exposing raw browser tokens in Redis.
    """

    def __init__(self, redis_client, encryption_key):
        self.redis = redis_client
        self.cipher = Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key)

    def _key(self, sid: str) -> str:
        """Returns the SHA-256 hashed Redis lookup key for the given opaque session token."""
        h = hashlib.sha256(sid.encode("utf-8")).hexdigest()
        return f"webbrief:credentials:{h}"

    def _legacy_key(self, sid: str) -> str:
        """Returns the legacy unhashed Redis key format for backwards compatibility."""
        return f"webbrief:credentials:{sid}"

    def save(self, sid: str, config: Dict[str, Any], preserve_created: bool = True) -> bool:
        """
        Encrypts and stores provider credentials indefinitely in Redis.
        Overwrites existing credentials for the same identifier without creating orphaned records.
        Returns True on successful storage, False on Redis, serialization, or unreadable state.
        """
        if not sid or not isinstance(config, dict):
            return False

        created_time = int(time.time())
        if preserve_created:
            status, existing_record = self.load_with_status(sid)
            # VLT-03: Never silently overwrite an unreadable or unavailable record
            if status == VaultStatus.UNAVAILABLE:
                logger.error("Refusing to save credentials: Redis is unavailable.")
                return False
            if status in (VaultStatus.DECRYPTION_ERROR, VaultStatus.CORRUPTED):
                logger.error("Refusing to save credentials: existing record is damaged or cannot be decrypted.")
                return False
            if status == VaultStatus.CONFIGURED and existing_record:
                if "_created" in existing_record:
                    created_time = int(existing_record["_created"])
                elif "created" in existing_record:
                    created_time = int(existing_record["created"])

        record = {
            "created": created_time,
            "_created": created_time,
            "_updated": int(time.time()),
            "config": config,
        }
        blob = self.cipher.encrypt(json.dumps(record).encode("utf-8"))

        hashed_key = self._key(sid)
        legacy_key = self._legacy_key(sid)
        try:
            # Indefinite persistence: set key WITHOUT TTL (no setex, no expire)
            res = self.redis.set(hashed_key, blob)
            if res is False:
                logger.error("Redis SET failed for key %s", hashed_key)
                return False

            # Clean up legacy unhashed key if it previously existed for this session
            if legacy_key != hashed_key:
                try:
                    self.redis.delete(legacy_key)
                except Exception as del_err:
                    logger.warning("Failed to clean up legacy key %s: %s", legacy_key, del_err)

            return True
        except RedisError as e:
            logger.error("Failed to store credentials in Redis vault: %s", e)
            return False

    def update(
        self,
        sid: str,
        updates: Dict[str, Any],
        remove_keys: Optional[list] = None,
        max_retries: int = 5,
    ) -> Tuple[bool, VaultStatus]:
        """
        Safely and atomically merges updates into existing credentials for a session.
        Uses Redis WATCH / MULTI / EXEC when available to prevent race conditions and lost updates.
        - Returns (False, VaultStatus.UNAVAILABLE) if Redis cannot be reached.
        - Returns (False, VaultStatus.DECRYPTION_ERROR / CORRUPTED) if existing record is damaged.
        - Merges fields into existing record when CONFIGURED.
        - Removes any keys specified in remove_keys.
        - Saves fresh record when NOT_CONFIGURED.
        Returns (success: bool, status: VaultStatus).
        """
        if not sid or not isinstance(updates, dict):
            return False, VaultStatus.NOT_CONFIGURED

        hashed_key = self._key(sid)
        legacy_key = self._legacy_key(sid)

        # Check if Redis client pipeline actually supports atomic watch transactions (VLT-02)
        can_pipeline = False
        if hasattr(self.redis, "pipeline"):
            try:
                p_test = self.redis.pipeline()
                can_pipeline = hasattr(p_test, "watch") and hasattr(p_test, "get") and hasattr(p_test, "set")
            except Exception:
                can_pipeline = False

        if can_pipeline:
            for attempt in range(max_retries):
                try:
                    pipe = self.redis.pipeline()
                    # VLT-01: Watch BOTH hashed_key and legacy_key before reading either!
                    if hasattr(pipe, "watch"):
                        if legacy_key != hashed_key:
                            pipe.watch(hashed_key, legacy_key)
                        else:
                            pipe.watch(hashed_key)

                    blob = pipe.get(hashed_key)
                    if not blob and legacy_key != hashed_key:
                        blob = pipe.get(legacy_key)

                    existing = {}
                    created_time = int(time.time())

                    if blob:
                        try:
                            record = json.loads(self.cipher.decrypt(blob))
                            existing = record.get("config", {})
                            if "_created" in record:
                                created_time = record["_created"]
                            elif "created" in record:
                                created_time = int(record["created"])
                        except InvalidToken:
                            try:
                                if hasattr(pipe, "unwatch"):
                                    pipe.unwatch()
                            except Exception:
                                pass
                            logger.warning("Stored credential ciphertext could not be decrypted.")
                            return False, VaultStatus.DECRYPTION_ERROR
                        except Exception as e:
                            try:
                                if hasattr(pipe, "unwatch"):
                                    pipe.unwatch()
                            except Exception:
                                pass
                            logger.warning("Unparseable vault record for session: %s", type(e).__name__)
                            return False, VaultStatus.CORRUPTED

                    merged = dict(existing)
                    merged.pop("_created", None)
                    merged.pop("created", None)
                    if remove_keys:
                        for rk in remove_keys:
                            merged.pop(rk, None)
                    merged.update(updates)

                    record = {
                        "created": created_time,
                        "_created": created_time,
                        "_updated": int(time.time()),
                        "config": merged,
                    }
                    new_blob = self.cipher.encrypt(json.dumps(record).encode("utf-8"))

                    if hasattr(pipe, "multi"):
                        pipe.multi()
                    pipe.set(hashed_key, new_blob)
                    try:
                        if legacy_key != hashed_key:
                            pipe.delete(legacy_key)
                    except Exception:
                        pass
                    pipe.execute()
                    return True, VaultStatus.CONFIGURED

                except WatchError:
                    if attempt == max_retries - 1:
                        logger.error("Concurrency conflict: maximum retries reached for session %s", sid)
                        return False, VaultStatus.UNAVAILABLE
                    continue
                except RedisError as e:
                    logger.error("Redis error during atomic credential update: %s", e)
                    return False, VaultStatus.UNAVAILABLE
                except Exception as e:
                    logger.error("Unexpected error in pipeline update: %s", e)
                    return False, VaultStatus.UNAVAILABLE

            return False, VaultStatus.UNAVAILABLE

        # Fallback only when client genuinely does not support Redis pipelines (e.g. basic mock)
        logger.warning("Redis client does not support atomic pipelines; falling back to checked read-merge-write.")
        status, existing = self.load_with_status(sid)
        if status == VaultStatus.UNAVAILABLE:
            logger.error("Refusing to update credentials: Redis storage is unavailable.")
            return False, VaultStatus.UNAVAILABLE
        if status in (VaultStatus.DECRYPTION_ERROR, VaultStatus.CORRUPTED):
            logger.error("Refusing to update credentials: existing record cannot be safely decrypted or parsed (%s).", status)
            return False, status

        merged = dict(existing) if status == VaultStatus.CONFIGURED and existing else {}
        merged.pop("_created", None)
        merged.pop("created", None)
        if remove_keys:
            for rk in remove_keys:
                merged.pop(rk, None)
        merged.update(updates)

        saved = self.save(sid, merged, preserve_created=True)
        return saved, (VaultStatus.CONFIGURED if saved else VaultStatus.UNAVAILABLE)

    def load_with_status(self, sid: str) -> Tuple[VaultStatus, Dict[str, Any]]:
        """
        Decrypts and returns provider credentials indefinitely stored in Redis along with explicit status:
        - (VaultStatus.NOT_CONFIGURED, {}) if sid is empty or key not in Redis
        - (VaultStatus.CONFIGURED, config) if record exists and decrypted cleanly
        - (VaultStatus.UNAVAILABLE, {}) if Redis communication fails (timeout, connection refused)
        - (VaultStatus.DECRYPTION_ERROR, {}) if Fernet decryption fails (key mismatch)
        - (VaultStatus.CORRUPTED, {}) if JSON payload is malformed
        """
        if not sid:
            return VaultStatus.NOT_CONFIGURED, {}

        hashed_key = self._key(sid)
        blob = None
        try:
            blob = self.redis.get(hashed_key)
            if not blob:
                # Check legacy unhashed key for seamless backward compatibility
                legacy_key = self._legacy_key(sid)
                blob = self.redis.get(legacy_key)
        except RedisError as e:
            logger.error("Failed to fetch credentials from Redis vault (Redis unavailable): %s", e)
            return VaultStatus.UNAVAILABLE, {}

        if not blob:
            return VaultStatus.NOT_CONFIGURED, {}

        try:
            record = json.loads(self.cipher.decrypt(blob))
            config = record.get("config", {})
            if "_created" in record:
                config["_created"] = record["_created"]
            if "created" in record:
                config["created"] = record["created"]
            return VaultStatus.CONFIGURED, config
        except InvalidToken:
            logger.warning("Stored credential ciphertext could not be decrypted with current Fernet key.")
            return VaultStatus.DECRYPTION_ERROR, {}
        except (ValueError, KeyError, TypeError) as e:
            logger.warning("Unparseable vault record for session: %s", type(e).__name__)
            return VaultStatus.CORRUPTED, {}

    def load(self, sid: str) -> Dict[str, Any]:
        """
        Decrypts and returns provider credentials indefinitely stored in Redis.
        Returns empty dictionary if record is missing, unavailable, or invalid.
        Use load_with_status() or load_or_raise() to distinguish error outcomes.
        """
        _, config = self.load_with_status(sid)
        return config

    def load_or_raise(self, sid: str) -> Dict[str, Any]:
        """
        Loads credentials or raises typed exceptions for specific failure modes:
        - VaultUnavailableError if Redis is down
        - VaultDecryptionError if Fernet key is invalid
        - VaultCorruptedError if JSON is corrupted
        Returns empty dict if genuinely NOT_CONFIGURED.
        """
        status, config = self.load_with_status(sid)
        if status == VaultStatus.UNAVAILABLE:
            raise VaultUnavailableError("Redis credential storage is currently unreachable.")
        elif status == VaultStatus.DECRYPTION_ERROR:
            raise VaultDecryptionError("Stored credentials could not be decrypted.")
        elif status == VaultStatus.CORRUPTED:
            raise VaultCorruptedError("Stored credential record is corrupted.")
        return config

    def delete(self, sid: str) -> bool:
        """
        Removes stored session credentials from Redis for both hashed and legacy keys.
        Returns True on successful deletion, False on Redis communication error.
        """
        if not sid:
            return True

        hashed_key = self._key(sid)
        legacy_key = self._legacy_key(sid)

        try:
            # Delete both hashed and legacy keys
            if hasattr(self.redis, "pipeline"):
                try:
                    pipe = self.redis.pipeline()
                    pipe.delete(hashed_key)
                    if legacy_key != hashed_key:
                        pipe.delete(legacy_key)
                    pipe.execute()
                    return True
                except (AttributeError, TypeError):
                    pass

            self.redis.delete(hashed_key)
            if legacy_key != hashed_key:
                self.redis.delete(legacy_key)
            return True
        except RedisError as e:
            logger.error("Failed to delete credentials from Redis vault: %s", e)
            return False


# Alias for explicit semantic usage
PersistentCredentialVault = TemporaryCredentialVault
