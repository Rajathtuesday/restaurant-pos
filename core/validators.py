from core.errors import UserError
import logging
import re
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

logger = logging.getLogger("pos.core")

def validate_image_size(image):
    file_size = image.file.size
    limit_mb = 2
    if file_size > limit_mb * 1024 * 1024:
        raise ValidationError(f"Max size of file is {limit_mb} MB")


def process_uploaded_image(file_field, max_dimension=800, quality=85):
    """
    Re-encodes an uploaded image field to WebP via Pillow, which is the
    actual content-type security boundary here (not the filename/extension,
    which is trivially spoofable) -- Pillow raises on anything it can't
    decode as a raster image, including an SVG carrying an inline <script>
    or an HTML file renamed to .jpg. Returns (filename, ContentFile) ready
    for `field.save(name, content, save=False)`.

    Raises ValidationError (not a silent no-op) on anything that isn't a
    genuine image, so callers must decide what "reject" means for their
    save flow -- do NOT catch broadly and keep the original raw upload on
    failure, since the original upload is exactly what's unvalidated.
    """
    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(file_field)
        img.verify()  # cheap structural check -- verify() closes the file handle
        file_field.seek(0)
        img = Image.open(file_field)  # re-open: verify() leaves the image unusable
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError("Uploaded file is not a valid image.") from exc

    output = BytesIO()
    img.save(output, format="WebP", quality=quality)
    output.seek(0)
    base_name = (file_field.name or "upload").rsplit(".", 1)[0]
    return f"{base_name}.webp", ContentFile(output.read())


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


def positive_int(value):
    """
    A positive whole number from JSON or a query string (7, "7", " 7 "),
    else None. For IDs and amounts that arrive from outside: a lookup like
    Outlet.objects.get(id="abc") raises instead of finding nothing, which in
    a webhook is a logged 500 any caller can trigger. JSON true/false are
    not numbers here, and neither are 7.5, "-7" or "7e2".
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = value.strip() if isinstance(value, str) else ""
    if text.isascii() and text.isdigit() and int(text) > 0:
        return int(text)
    return None


class NumberInputError(UserError, ValueError):
    """A number from a form or JSON body that the person has to correct.
    str(e) says what to fix and is safe to show as it is."""


def read_number(raw, label, *, minimum=0, maximum=None, places=None, blank=None, field=None):
    """
    A Decimal from a form or JSON value, checked, or NumberInputError.

    Python's Decimal happily reads "NaN", "Infinity" and "1e20", and a bare
    Decimal() parse let all of them, and negatives, through: a negative
    parcel charge or cost price was saved (quietly wrong money), and NaN or a
    huge price reached the database and became a 500. Every number staff type
    in goes through here instead:

      * must be a finite number with at most `places` decimals;
      * must be at least `minimum` (0 by default) and at most `maximum`;
      * blank (None or "") returns `blank`, or is an error if `blank` is None
        and the caller said nothing (pass blank=Decimal("0") to allow it);
      * `field`, the model DecimalField it is saved to, gives the decimals
        (unless `places` says otherwise) and the largest value its column
        holds, so a huge number is refused here, not by the database.
    """
    from decimal import Decimal, InvalidOperation

    column_top = None
    if field is not None:
        if places is None:
            places = field.decimal_places
        column_top = (Decimal(10) ** (field.max_digits - field.decimal_places)
                      - Decimal(1).scaleb(-field.decimal_places))
    if places is None:
        places = 2

    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if blank is not None:
            return blank
        raise NumberInputError(f"{label} is required.")
    if isinstance(raw, bool):
        raise NumberInputError(f"{label} must be a number.")
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise NumberInputError(f"{label} must be a number.")
    if not value.is_finite():
        raise NumberInputError(f"{label} must be a number.")
    if minimum is not None and value < minimum:
        raise NumberInputError(f"{label} can't be negative." if minimum == 0
                               else f"{label} must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise NumberInputError(f"{label} can't be more than {maximum}.")
    if column_top is not None and value > column_top:
        raise NumberInputError(f"{label} is too large.")
    step = Decimal(1).scaleb(-places)
    try:
        rounded = value.quantize(step)
    except InvalidOperation:            # too many digits to hold at all
        raise NumberInputError(f"{label} is too large.")
    if value != rounded:
        raise NumberInputError(f"{label} can have at most {places} decimal places.")
    return rounded
