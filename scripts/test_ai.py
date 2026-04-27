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
    print("🚀 Initializing Gemini AI Test...")
    ai = AIService()
    
    if not ai.api_key:
        print("❌ Error: GOOGLE_API_KEY not found in .env")
        return

    print(f"📡 Connecting to Gemini with key: {ai.api_key[:8]}...")
    
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
        print("2. You might have exceeded your free quota.")
        print("3. Check if 'google-genai' is installed correctly.")

if __name__ == "__main__":
    test_api()
