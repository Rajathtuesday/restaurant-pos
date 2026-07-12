try:
    from google import genai
    from google.genai import types
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

from PIL import Image
import io

from django.conf import settings
import logging

logger = logging.getLogger("pos.ai")

class AIService:
    def __init__(self):
        import os
        self.api_key = os.getenv("GOOGLE_API_KEY")
        self.client = None

        # Log the SPECIFIC reason the client can't be built, so the server logs
        # say exactly what to fix instead of a generic "no AI key". The #1 cause
        # in practice is a stale process: .env was updated but the worker that
        # runs the import (celery for image import, gunicorn for the sync
        # fallback) was never restarted, so it still has the old environment.
        if not HAS_GENAI:
            logger.warning(
                "AI disabled: the 'google-genai' package is not installed in this "
                "environment. Run: pip install -r requirements.txt"
            )
            return
        if not self.api_key:
            logger.warning(
                "AI disabled: GOOGLE_API_KEY is not set in THIS process's environment. "
                "Set it in .env, then RESTART the service that runs the import "
                "(celery for image import, gunicorn for the sync fallback)."
            )
            return
        try:
            self.client = genai.Client(api_key=self.api_key)
            logger.info("AI client initialized (key ...%s).", self.api_key[-4:])
        except Exception as e:
            logger.error("Failed to initialize AI client — check the key is valid: %s", e)

    def _resize_image(self, image_bytes, max_size=(1024, 1024)):
        """Re-encode any image to a clean, right-sized RGB JPEG.

        Raises if the bytes aren't a readable image (e.g. an iPhone HEIC, or a
        corrupt upload). The old version swallowed that and returned the ORIGINAL
        bytes while still labelling them image/jpeg — so Gemini received
        non-JPEG data claiming to be JPEG and replied with the opaque
        "Unable to process input image" 400. Better to fail here and let the
        caller show a "use a JPG/PNG" message.
        """
        from PIL import ImageOps
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)          # honor phone-camera rotation
        if img.mode != "RGB":
            img = img.convert("RGB")                # JPEG needs RGB (handles P/RGBA/CMYK/LA/…)
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=80, optimize=True)
        return output.getvalue()

    # -------------------------------------------------------
    # FILE ROUTING — turn any uploaded file into something Gemini can read
    # -------------------------------------------------------

    def _file_to_content(self, file_bytes, mime_type):
        """Return a Gemini content item for an uploaded menu file.

        - Images  -> re-encoded JPEG Part
        - PDFs    -> PDF Part (Gemini reads text AND scanned/image PDFs natively)
        - Word    -> extracted text
        - Excel   -> extracted text
        - text/csv-> decoded text
        Raises a friendly error for anything unreadable.
        """
        mt = (mime_type or "").lower()

        # PDF — sniff the magic bytes too, in case the browser sent no mime type
        if mt == "application/pdf" or file_bytes[:5] == b"%PDF-":
            return types.Part.from_bytes(data=file_bytes, mime_type="application/pdf")

        # Word (.docx / .doc)
        if "wordprocessingml" in mt or mt == "application/msword":
            return "Menu text extracted from a Word document:\n" + self._extract_docx(file_bytes)

        # Excel (.xlsx / .xls)
        if "spreadsheetml" in mt or "ms-excel" in mt:
            return "Menu text extracted from a spreadsheet:\n" + self._extract_xlsx(file_bytes)

        # Plain text / CSV
        if mt.startswith("text/"):
            return "Menu text:\n" + file_bytes.decode("utf-8", errors="replace")

        # Otherwise assume it's a photo (explicit image/* or unknown type).
        try:
            return types.Part.from_bytes(
                data=self._resize_image(file_bytes), mime_type="image/jpeg"
            )
        except Exception as e:
            logger.warning("Unreadable upload (mime=%s): %s", mime_type, e)
            raise Exception(
                "Couldn't read that file. Please upload a photo (JPG/PNG), a PDF, "
                "a Word document (.docx), an Excel sheet (.xlsx), or plain text/CSV. "
                "(iPhone HEIC photos aren't supported — screenshot the menu instead.)"
            )

    def _extract_docx(self, file_bytes):
        """Pull all paragraph + table text out of a .docx."""
        try:
            from docx import Document
        except ImportError:
            raise Exception(
                "Word import isn't available on the server yet. Save the menu as "
                "a PDF or take a screenshot, and upload that instead."
            )
        doc = Document(io.BytesIO(file_bytes))
        lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    lines.append("  ".join(cells))
        return "\n".join(lines)

    def _extract_xlsx(self, file_bytes):
        """Flatten every non-empty cell of every sheet into text (openpyxl is
        already a dependency for the GSTR-1 export)."""
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        lines = []
        try:
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                    if cells:
                        lines.append("  ".join(cells))
        finally:
            wb.close()
        return "\n".join(lines)

    def parse_menu(self, text=None, image_bytes=None, mime_type=None):
        """
        Parses menu from text or image. 
        Falls back to manual regex parsing if AI is not configured and only text is provided.
        """
        # Improved check: only use manual fallback if we have actual text and NO image
        can_manual = bool(text and text.strip() and not image_bytes)
        
        if not self.client:
            if can_manual:
                logger.info("Google API Key not found. Falling back to manual text parser.")
                return self._manual_text_parse(text)
            
            # If they provided an image but we have no API key
            if image_bytes:
                raise Exception("Image parsing requires an AI activation key. Please add it to your configuration.")
            
            # If they provided neither or empty text
            raise Exception("No menu data provided to parse.")

        prompt = """
        Analyze this restaurant menu and extract all items. 
        Format your response as a valid JSON list of objects. Each object represents a category.
        Each category object must have:
        1. "category": String (name of the category, e.g., "Starters")
        2. "items": List of objects, each with "name" (String) and "price" (Float/Number).
        
        If you find a description, ignore it. Only extract name and price.
        If no price is found, use 0.
        Output ONLY the raw JSON list. No markdown, no backticks.
        """

        contents = [prompt]
        if text and text.strip():
            contents.append(f"Here is the menu text:\n{text}")
        
        if image_bytes:
            # `image_bytes` is really "the uploaded file" — could be an image, a
            # PDF, a Word doc, an Excel sheet or plain text. _file_to_content
            # routes each to the right thing (a Gemini Part for images/PDFs, or
            # extracted text for Office docs).
            contents.append(self._file_to_content(image_bytes, mime_type))

        try:
            response = self.client.models.generate_content(
                model='gemini-flash-latest',
                contents=contents
            )
            import json
            
            # The new SDK response structure: response.text or response.candidates[0].content.parts[0].text
            res_text = response.text or ""
            # Strip potential markdown backticks
            raw_json = res_text.strip().replace("```json", "").replace("```", "")
            return json.loads(raw_json)
        except Exception as e:
            logger.error("In-build AI API Error: %s", e)
            if text:
                logger.info("Retrying with manual parser after API error.")
                return self._manual_text_parse(text)
            raise e

    def _manual_text_parse(self, text):
        """
        Fallback parser using regex to extract categories and items from raw text.
        """
        import re
        if not text: return []
        
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        structured = []
        current_category = {"category": "General", "items": []}
        
        for line in lines:
            # Detect category: All caps, or ends with colon, or short line with no digits
            is_cat = (line.isupper() and len(line) > 3) or line.endswith(':') or (not any(c.isdigit() for c in line) and len(line) < 25)
            
            if is_cat:
                if current_category["items"]:
                    structured.append(current_category)
                current_category = {"category": line.rstrip(':').strip().title(), "items": []}
                continue
            
            # Match "Item Name 123.45" or "Item Name - 123"
            match = re.search(r'(.*?)(?:[:\-\s]+)(\d+(?:\.\d+)?)$', line)
            if match:
                name, price = match.groups()
                current_category["items"].append({
                    "name": name.strip(),
                    "price": float(price)
                })
            else:
                # No price found, add as item with price 0
                current_category["items"].append({
                    "name": line.strip(),
                    "price": 0
                })

        if current_category["items"]:
            structured.append(current_category)
        return structured

    def suggest_pricing(self, dish_name, ingredients_with_costs):
        """
        Suggests pricing for a dish based on ingredient costs and 30% food cost target.
        """
        if not self.client: return None
        
        prompt = f"Calculate the suggested menu price for '{dish_name}'. Ingredients and their total cost in this dish: {ingredients_with_costs}. Aim for a 30% food cost percentage. Add a summary of why."
        try:
            response = self.client.models.generate_content(
                model='gemini-flash-latest',
                contents=prompt
            )
            return response.text
        except Exception as e:
            logger.error("AI pricing suggestion error: %s", e)
            return None
