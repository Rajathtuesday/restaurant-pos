import os
try:
    from google import genai
    from google.genai import types
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

from django.conf import settings
import logging

logger = logging.getLogger("pos.ai")

class AIService:
    def __init__(self):
        self.api_key = os.getenv("GOOGLE_API_KEY")
        self.client = None
        if self.api_key and HAS_GENAI:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                logger.error(f"Failed to initialize Gemini Client: {e}")

    def parse_menu(self, text=None, image_bytes=None, mime_type=None):
        """
        Parses menu from text or image. 
        Falls back to manual regex parsing if Gemini is not configured and only text is provided.
        """
        # Improved check: only use manual fallback if we have actual text and NO image
        can_manual = bool(text and text.strip() and not image_bytes)
        
        if not self.client:
            if can_manual:
                logger.info("Google API Key not found. Falling back to manual text parser.")
                return self._manual_text_parse(text)
            
            # If they provided an image but we have no API key
            if image_bytes:
                raise Exception("Vision/Image parsing requires a Google API Key. Please add GOOGLE_API_KEY to your .env file or use text-only import.")
            
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
            contents.append(
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type=mime_type or "image/jpeg"
                )
            )

        try:
            response = self.client.models.generate_content(
                model='gemini-1.5-flash',
                contents=contents
            )
            import json
            
            # The new SDK response structure: response.text or response.candidates[0].content.parts[0].text
            res_text = response.text or ""
            # Strip potential markdown backticks
            raw_json = res_text.strip().replace("```json", "").replace("```", "")
            return json.loads(raw_json)
        except Exception as e:
            logger.error(f"Gemini API Error: {e}")
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
                model='gemini-1.5-flash',
                contents=prompt
            )
            return response.text
        except Exception as e:
            logger.error(f"Gemini pricing suggestion error: {e}")
            return None
