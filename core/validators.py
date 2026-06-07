import re

from django.core.exceptions import ValidationError

def validate_image_size(image):
    file_size = image.file.size
    limit_mb = 2
    if file_size > limit_mb * 1024 * 1024:
        raise ValidationError(f"Max size of file is {limit_mb} MB")


def normalize_phone(raw):
    """
    Normalise an Indian mobile number to its 10-digit form.

    Accepts optional '+91', '91', or a single leading '0' prefix and surrounding
    spaces/dashes. Returns the 10-digit string, or None for empty input.
    Raises ValidationError if the result is not a valid Indian mobile
    (10 digits, first digit 6-9).

    Phone is optional everywhere it's used — empty/None passes through as None.
    """
    if raw is None:
        return None
    s = re.sub(r"[\s\-()]", "", str(raw))
    if not s:
        return None
    # Strip common country/trunk prefixes
    if s.startswith("+91"):
        s = s[3:]
    elif s.startswith("91") and len(s) == 12:
        s = s[2:]
    elif s.startswith("0") and len(s) == 11:
        s = s[1:]
    if not re.fullmatch(r"[6-9]\d{9}", s):
        raise ValidationError("Enter a valid 10-digit Indian mobile number.")
    return s
