"""
Uploaded images: dish photos and restaurant logos (core/validators.py
process_uploaded_image, called from MenuItem.save and Tenant.save).

Found 3 Nov 2026: a phone photo uploaded as a dish photo was stored on its
side. Phones save the pixels sideways and add an EXIF tag saying how to turn
them; the upload is re-encoded to WebP, which carries no EXIF, so the tag
was lost and the dish showed rotated. The AI menu import already turned
photos upright (core/ai_service.py). These tests also pin the security side
of the same function, which had no test: anything that isn't a real image
(an SVG with a script, an HTML page renamed .jpg) is refused, not stored.

Run: python manage.py test core.tests.test_image_uploads
"""
import io
import shutil
import tempfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image

from core.validators import process_uploaded_image
from menu.models import MenuCategory, MenuItem
from tenants.models import Outlet, Tenant

ORIENTATION = 0x0112      # the EXIF tag phones use
ROTATE_90_CW = 6          # "turn me 90 degrees clockwise to show me"


def phone_photo(width=1600, height=1200, orientation=ROTATE_90_CW):
    """A JPEG stored sideways, the way a phone saves a portrait photo: the
    pixels are landscape, the EXIF tag says turn them. The left edge is red,
    so the test can also check which way it was turned."""
    img = Image.new("RGB", (width, height), (240, 230, 210))
    img.paste((220, 0, 0), (0, 0, width // 10, height))
    exif = Image.Exif()
    if orientation:
        exif[ORIENTATION] = orientation
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90, exif=exif)
    return buf.getvalue()


def processed(data, name="dish.jpg"):
    filename, content = process_uploaded_image(SimpleUploadedFile(name, data))
    return filename, Image.open(io.BytesIO(content.read()))


def is_red(pixel):
    r, g, b = pixel[:3]
    return r > 180 and g < 60 and b < 60


class ProcessUploadedImageTest(SimpleTestCase):

    def test_a_sideways_phone_photo_is_stood_upright(self):
        _, img = processed(phone_photo())

        self.assertEqual(img.size, (600, 800))      # portrait, as the phone showed it
        # Turned 90 degrees clockwise: the red left edge is now along the top.
        self.assertTrue(is_red(img.getpixel((300, 10))))
        self.assertFalse(is_red(img.getpixel((10, 400))))

    def test_a_photo_without_the_tag_keeps_its_shape(self):
        _, img = processed(phone_photo(orientation=None))

        self.assertEqual(img.size, (800, 600))
        self.assertTrue(is_red(img.getpixel((10, 300))))

    def test_it_is_saved_as_webp_no_bigger_than_800(self):
        filename, img = processed(phone_photo(4000, 3000), name="IMG_2041.jpeg")

        self.assertEqual(filename, "IMG_2041.webp")
        self.assertEqual(img.format, "WEBP")
        self.assertEqual(img.size, (600, 800))

    def test_files_that_are_not_images_are_refused(self):
        not_images = {
            "an SVG with a script, renamed": b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            "an HTML page, renamed": b"<html><body>hello</body></html>",
            "a photo cut off half way": phone_photo()[:5000],
            "an empty file": b"",
        }
        for what, data in not_images.items():
            with self.subTest(what):
                with self.assertRaises(ValidationError):
                    process_uploaded_image(SimpleUploadedFile("dish.jpg", data))


class UploadedThroughTheModelsTest(TestCase):
    """The same, through the two real save paths: a dish photo and a logo."""

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.tenant = Tenant.objects.create(name="Photo Test Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")

    def test_a_dish_photo_from_a_phone_is_stored_upright(self):
        category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        dish = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=category, name="Dosa", price=90,
                                       image=SimpleUploadedFile("dosa.jpg", phone_photo(), content_type="image/jpeg"))

        dish.refresh_from_db()
        self.assertTrue(dish.image.name.endswith(".webp"))
        with dish.image.open("rb") as f:
            self.assertEqual(Image.open(f).size, (600, 800))

    def test_a_logo_from_a_phone_is_stored_upright(self):
        self.tenant.logo = SimpleUploadedFile("logo.jpg", phone_photo(), content_type="image/jpeg")
        self.tenant.save()

        self.tenant.refresh_from_db()
        self.assertTrue(self.tenant.logo.name.endswith(".webp"))
        with self.tenant.logo.open("rb") as f:
            self.assertEqual(Image.open(f).size, (600, 800))
