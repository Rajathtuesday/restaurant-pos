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
