# core/fields.py
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


def _fernet() -> Fernet:
    return Fernet(settings.FIELD_ENCRYPTION_KEY)


class EncryptedCharField(models.CharField):
    """
    A CharField that's encrypted at rest (Fernet/AES) and transparently
    decrypted on read. Blank/empty values pass through untouched — there's
    nothing worth encrypting in an empty string, and it lets the field's
    existing blank=True/default="" usage keep working unchanged.
    """

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value:
            return value
        return _fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if not value:
            return value
        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            # Value predates encryption (or the key changed) — surface it
            # as-is rather than crashing every read of this row.
            return value
