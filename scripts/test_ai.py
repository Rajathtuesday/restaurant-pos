import os
import sys
import django
from dotenv import load_dotenv

# Setup path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

# Load environment
load_dotenv(os.path.join(BASE_DIR, ".env"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from core.ai_service import AIService

def test_api():
    print("🚀 Initializing Claude AI Test...")
    ai = AIService()
    
    if not ai.client:
        print("❌ Error: ANTHROPIC_API_KEY not found in .env")
        return

    # No part of the key is printed: a pasted terminal log or screenshot
    # should never carry any of it.
    print("📡 Connecting to Claude (key loaded from .env)")
    
    try:
        # Test basic generation
        print("📝 Testing Menu Parsing logic...")
        sample_text = "Chicken Biryani 250\nSoft Drink 50"
        result = ai.parse_menu(text=sample_text)
        
        print("✅ Connection Successful!")
        print("-" * 30)
        print("Parsed Data Sample:")
        print(result)
        print("-" * 30)
        print("🎉 AI Service is ready to use!")
        
    except Exception as e:
        print(f"❌ API Error: {str(e)}")
        print("\nPossible issues:")
        print("1. Your API key might be invalid.")
        print("2. The account may be out of credit or rate limited.")
        print("3. Check if 'anthropic' is installed correctly.")

if __name__ == "__main__":
    test_api()
