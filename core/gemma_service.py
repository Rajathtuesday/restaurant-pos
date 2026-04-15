import os
import requests
import json
import logging

logger = logging.getLogger("pos.ai")

class GemmaService:
    """
    Local AI Service using Gemma (via Ollama API).
    Assumes Ollama is running on localhost:11434.
    """
    def __init__(self, model="gemma2:2b"):
        self.url = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
        self.model = model

    def chat(self, prompt, system_prompt="You are a helpful POS assistant."):
        payload = {
            "model": self.model,
            "prompt": f"{system_prompt}\n\nUser: {prompt}\nAssistant:",
            "stream": False
        }
        try:
            response = requests.post(self.url, json=payload, timeout=30)
            if response.status_code == 200:
                return response.json().get("response")
            return f"Error: Local AI returned {response.status_code}"
        except Exception as e:
            logger.error(f"Local AI (Gemma) connection failed: {e}")
            return "Local AI is currently offline. Please ensure Ollama is running."

    def simplify_text(self, text):
        """Used for simple tasks like cleaning up menu descriptions or notes."""
        prompt = f"Summarize and clean up this restaurant order note to be concise: {text}"
        return self.chat(prompt)

class AIOrchestrator:
    """
    Routes tasks to either Gemini (Cloud) or Gemma (Local).
    """
    def __init__(self):
        from core.ai_service import AIService
        self.gemini = AIService()
        self.gemma = GemmaService()

    def parse_menu(self, text=None, image_bytes=None, mime_type=None):
        # Vision tasks always go to Gemini
        return self.gemini.parse_menu(text, image_bytes, mime_type)

    def simple_assistant(self, query):
        # Text-only simple queries go to Local Gemma to save costs/latency
        return self.gemma.chat(query)
