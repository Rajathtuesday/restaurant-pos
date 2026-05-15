# 🔑 How to Get Your Google Gemini API Key

Follow these steps to generate a new API key for your Restaurant POS system.

## 1. Access Google AI Studio
- Go to [**Google AI Studio**](https://aistudio.google.com/) (formerly MakerSuite).
- Sign in with your Google Account.

## 2. Generate the Key
- On the left-hand sidebar, click on the **"Get API key"** button (🔑 icon).
- Click **"Create API key in new project"**.
- Once the key is generated, click **Copy**.

## 3. Configure Your POS System
- Open your project's `.env` file at `f:\pos\.env`.
- Look for the `GOOGLE_API_KEY` line.
- Replace the existing value with your new key:
  ```env
  GOOGLE_API_KEY=AIzaSy...your_new_key_here
  ```
- Save the file.

## 4. Test the Connection
You can verify if the key is working by running the following command in your terminal:
```bash
.venv\Scripts\python.exe scripts\test_ai.py
```

---

### 💡 Important Notes:
- **Free Tier:** Google currently offers a free tier for Gemini 1.5 Flash (up to 15 requests per minute).
- **Security:** Never share your API key or commit your `.env` file to public repositories like GitHub.
