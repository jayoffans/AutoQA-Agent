"""AES-256-GCM envelopes with per-record associated data; no reversible password storage."""
import base64
import hashlib
import hmac
import json
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretBox:
    def __init__(self, master_key):
        self.cipher = AESGCM(base64.urlsafe_b64decode(master_key))

    def encrypt(self, value, scope):
        nonce = secrets.token_bytes(12)
        blob = self.cipher.encrypt(nonce, json.dumps(value).encode(), scope.encode())
        return "v1:" + base64.urlsafe_b64encode(nonce + blob).decode()

    def decrypt(self, value, scope):
        if not value.startswith("v1:"):
            raise ValueError("Unsupported secret envelope")
        blob = base64.urlsafe_b64decode(value[3:])
        return json.loads(self.cipher.decrypt(blob[:12], blob[12:], scope.encode()))


def mask_key(key):
    # Very short fixture keys are never disclosed in full either.
    return "sk-••••••••" + (key[-4:] if len(key) > 8 else "••••")


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    derived = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return "scrypt$" + salt + "$" + derived.hex()


def check_password(password, saved):
    return hmac.compare_digest(hash_password(password, saved.split("$")[1]), saved)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def scrub(value, secrets_to_hide):
    from .security import redact
    def walk(item):
        if isinstance(item, dict):
            return {k: walk(v) for k, v in item.items()}
        if isinstance(item, list):
            return [walk(v) for v in item]
        if isinstance(item, str):
            for secret in sorted(set(secrets_to_hide), key=len, reverse=True):
                if secret:
                    item = item.replace(secret, "[REDACTED]")
        return item
    return walk(redact(value))
