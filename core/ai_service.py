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
            
            # Match "Item Name 123.45" or "Item Name - 123". Deliberately not
            # a single regex like r'(.*?)(?:[:\-\s]+)(\d+...)$' -- that shape
            # lets the non-greedy name group and the separator class both
            # match '-'/whitespace, so the engine can split them ambiguously
            # in exponentially many ways on a pathological line (e.g. a long
            # run of dashes/spaces with no trailing digits), which is exactly
            # the ReDoS CodeQL flagged. A plain trailing-digits match has no
            # such ambiguity, so peel the price off with that, then take
            # whatever's left as the name with ordinary string slicing.
            price_match = re.search(r'(\d+(?:\.\d+)?)$', line)
            if price_match:
                name = line[:price_match.start()].strip(' \t:-')
                current_category["items"].append({
                    "name": name,
                    "price": float(price_match.group(1))
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

    def parse_recipe(self, text=None, image_bytes=None, mime_type=None):
        """
        Extracts a flat ingredient list from a recipe document/photo: each
        line is the ingredient name and the quantity exactly as written
        ("1 cup", "a pinch", "200g") — no unit conversion, no inventory
        matching. Those are separate, independently-debuggable steps done by
        inventory/recipe_unit_table.py and match_ingredients() below, so a
        bad extraction and a bad match/conversion never get tangled together
        in one opaque failure.

        Unlike parse_menu, there is no manual-regex fallback — a recipe's
        prose/format varies far more than a price list, so without AI this
        just raises a clear "AI not configured" error rather than guessing.
        """
        if not self.client:
            raise Exception(
                "AI recipe import requires an AI activation key. Please add it to your configuration."
            )
        if not (text and text.strip()) and not image_bytes:
            raise Exception("No recipe document provided to parse.")

        prompt = """
        You are extracting the ingredient list from a recipe document for ONE dish.
        The document may be messy: handwritten, split into sections (e.g. "For the
        marinade:", "For the gravy:"), or written as prose rather than a clean list.
        Read the whole thing and pull out every ingredient, from every section.

        Ignore preparation steps, cooking instructions, serving suggestions, and
        anything that isn't an ingredient with a quantity.

        Format your response as a valid JSON list of objects, one per ingredient:
        1. "ingredient": String — the ingredient name only (e.g. "Onion", not "2 medium onions")
        2. "quantity_text": String — the quantity EXACTLY as written in the source,
           unmodified (e.g. "1 cup", "2 medium", "a pinch", "200g", "1/2 tsp").
           Do not convert, round, or normalize it — copy it verbatim.

        If the same ingredient appears more than once (e.g. once in a marinade and
        once as a garnish), output it as two separate entries rather than combining them.

        Output ONLY the raw JSON list. No markdown, no backticks, no commentary.
        """

        contents = [prompt]
        if text and text.strip():
            contents.append(f"Here is the recipe text:\n{text}")
        if image_bytes:
            contents.append(self._file_to_content(image_bytes, mime_type))

        import json
        try:
            response = self.client.models.generate_content(
                model='gemini-flash-latest',
                contents=contents
            )
            res_text = response.text or ""
            raw_json = res_text.strip().replace("```json", "").replace("```", "")
            parsed = json.loads(raw_json)
            if not isinstance(parsed, list):
                raise ValueError("Expected a JSON list of ingredients")
            return parsed
        except Exception as e:
            logger.error("AI recipe extraction error: %s", e)
            raise Exception("Couldn't read that recipe. Try a clearer photo or a text/PDF export.")

    def match_ingredients(self, unmatched_names, inventory_names):
        """
        One batched call resolving every ingredient name rapidfuzz couldn't
        confidently match, against this tenant+outlet's real inventory item
        names. Deliberately a single call for the whole leftover list (not
        one call per ingredient) to keep cost/latency bounded.

        Returns {ingredient_name: matched_inventory_name_or_None}. Any name
        missing from the response (bad JSON, Gemini omitted it, etc.) is
        treated as unmatched by the caller — never assumed matched.
        """
        import json

        if not unmatched_names:
            return {}
        if not self.client or not inventory_names:
            return {name: None for name in unmatched_names}

        prompt = f"""
        You are matching recipe ingredient names to a restaurant's existing
        inventory item names. For each ingredient below, pick the inventory
        item name that refers to the SAME real-world ingredient (matching
        synonyms/variants is fine, e.g. "cream" -> "Heavy Cream"), or null if
        none of the inventory items are actually the same ingredient. Do not
        pick a loosely related item just because no better option exists —
        null is the correct answer when unsure.

        Ingredients to match: {json.dumps(unmatched_names)}

        Inventory item names to match against: {json.dumps(inventory_names)}

        Respond with ONLY a raw JSON object mapping each ingredient name (exactly
        as given above) to either a matching inventory item name (exactly as
        given above) or null. No markdown, no backticks, no commentary.
        """

        try:
            response = self.client.models.generate_content(
                model='gemini-flash-latest',
                contents=[prompt]
            )
            res_text = response.text or ""
            raw_json = res_text.strip().replace("```json", "").replace("```", "")
            parsed = json.loads(raw_json)
            if not isinstance(parsed, dict):
                raise ValueError("Expected a JSON object")
            # Only trust names that were actually offered, matched to names
            # that actually exist in inventory — anything else falls back to
            # unmatched rather than trusting an AI-invented name.
            inventory_set = set(inventory_names)
            result = {}
            for name in unmatched_names:
                match = parsed.get(name)
                result[name] = match if match in inventory_set else None
            return result
        except Exception as e:
            logger.error("AI ingredient matching error: %s", e)
            return {name: None for name in unmatched_names}

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
