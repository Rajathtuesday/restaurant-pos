import os
import google.generativeai as genai
from django.conf import settings

class AIService:
    def __init__(self):
        api_key = os.getenv("GOOGLE_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel('gemini-1.5-flash')
        else:
            self.model = None

    def parse_menu(self, text=None, image_bytes=None, mime_type=None):
        """
        Parses menu from text or image using Gemini.
        Returns a structured list of categories with items and prices.
        """
        if not self.model:
            raise Exception("Google API Key not configured. Please add GOOGLE_API_KEY to your .env file.")

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
        if text:
            contents.append(f"Here is the menu text:\n{text}")
        if image_bytes:
            contents.append({
                "mime_type": mime_type or "image/jpeg",
                "data": image_bytes
            })

        response = self.model.generate_content(contents)
        import json
        import re
        
        # Strip potential markdown backticks
        raw_json = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(raw_json)

    def suggest_pricing(self, dish_name, ingredients_with_costs):
        """
        Suggests pricing for a dish based on ingredient costs and 30% food cost target.
        ingredients_with_costs: list of {"name": str, "cost": float}
        """
        if not self.model: return None
        
        prompt = f"Calculate the suggested menu price for '{dish_name}'. Ingredients and their total cost in this dish: {ingredients_with_costs}. Aim for a 30% food cost percentage. Add a summary of why."
        response = self.model.generate_content(prompt)
        return response.text
