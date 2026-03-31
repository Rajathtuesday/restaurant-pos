# 🤖 AI Integration Guide — Restaurant POS System

A comprehensive overview of how Artificial Intelligence can supercharge your Fine Dining POS.

---

## 🏗️ What Your Current System Has

Before adding AI, here's what is **already in place**:

| Module | What it does |
|---|---|
| **AI Smart Menu Importer** | Pastes raw text → auto-detects categories & items |
| **Category Smart Mapper** | Fuzzy-maps imported categories to existing ones |
| **Action Logging** | Logs every user action with timestamp to `pos.log` |
| **Real-time Polling** | Waiter dashboard heartbeat (6s) |

---

## 🧠 AI Types That Can Be Integrated

---

### 1. 🍽️ Generative AI — Menu & Content Assistant
**Provider**: Google Gemini, OpenAI GPT-4o, Claude

**What it does:**
- Take a photo or PDF of your physical menu and extract all dishes, categories, and prices automatically using **Vision AI**.
- Gen AI can write beautiful dish descriptions for your QR menu (e.g., "A velvety slow-cooked lamb shank glazed with saffron jus").
- Suggest new seasonal menu combinations based on available inventory.

**How to integrate:**

```python
# In menu/views.py — AI Menu OCR Import
import google.generativeai as genai

genai.configure(api_key="YOUR_GEMINI_API_KEY")

def ai_image_menu_import(request):
    image = request.FILES["menu_photo"]
    model = genai.GenerativeModel("gemini-1.5-flash")
    
    prompt = """
    Extract all menu items from this image.
    Return JSON in this exact format:
    {"categories": [{"name": "Starters", "items": [{"name": "Soup", "price": 150}]}]}
    """
    response = model.generate_content([prompt, image.read()])
    # Parse and bulk-create into DB
```

**Your Existing Hook**: The `ai_menu_importer` view in `menu/views.py` is already built. We just swap the text parser for a Gemini Vision call.

---

### 2. 📊 Predictive AI — Sales Forecasting
**Provider**: Google Vertex AI, AWS SageMaker, or simple `scikit-learn` locally

**What it does:**
- Analyses your historical `Order` table data (which you already log!)
- Predicts **tomorrow's busiest hours** so you can schedule staff proactively.
- Forecasts which dishes will sell most on a given day (weekend vs weekday, festival days).
- Suggests how much stock to order before the week. AI-based **"What to Restock Today"** report.

**Data it reads from your DB:**
```sql
-- Already available in your models!
SELECT menu_item.name, COUNT(*), SUM(orders_orderitem.quantity)
FROM orders_orderitem
JOIN menu_menuitem ON menu_item.id = orders_orderitem.menu_item_id
WHERE orders_order.created_at >= NOW() - INTERVAL '30 days'
GROUP BY menu_item.name
ORDER BY COUNT(*) DESC;
```

**How to integrate (simple approach):**

```python
# reports/ai_insights.py
from django.db.models import Sum, Count
from orders.models import OrderItem
from datetime import datetime, timedelta

def get_top_items(user, days=30):
    cutoff = datetime.now() - timedelta(days=days)
    return (
        OrderItem.objects
        .filter(order__tenant=user.tenant, order__created_at__gte=cutoff)
        .values("menu_item__name")
        .annotate(total=Sum("quantity"))
        .order_by("-total")[:10]
    )
```

This feeds directly into your **Reports Dashboard** as an "AI Insights" card.

---

### 3. 🗣️ Conversational AI — Manager ChatBot
**Provider**: OpenAI GPT-4, Gemini, Llama 3 (local/private)

**What it does:**
- A floating chatbot on the Dashboard/Manager panel.
- Manager can ask questions in plain English:
  - *"What was my revenue last Sunday?"*
  - *"Which table had the most orders today?"*
  - *"Show me items that have never been ordered"*
- AI translates natural language → your database query → plain English answer.

**How to integrate (NL → SQL approach):**

```python
# New endpoint: reports/views.py

def ai_query(request):
    question = request.POST.get("question")
    
    # 1. Ask Gemini to write the SQL for you
    prompt = f"""
    You are a restaurant POS SQL expert. Schema:
    - orders_order: id, grand_total, created_at, status
    - orders_orderitem: order_id, menu_item_id, quantity
    - menu_menuitem: id, name, price, category_id
    
    Write a safe SELECT query for: "{question}"
    Tenant filter: tenant_id = {request.user.tenant.id}
    Return ONLY SQL, no explanation.
    """
    sql = gemini.generate(prompt)
    
    # 2. Execute safely with parameterized query
    # 3. Return result to manager in friendly text
```

---

### 4. 🎯 Recommendation AI — Upselling Engine
**Provider**: Simple collaborative filtering (no API needed) or Google Recommendations AI

**What it does:**
- When a waiter is taking an order on the billing screen: **"Customers who order Butter Chicken also order Garlic Naan 78% of the time."**
- Automatically surfaces this suggestion as a soft popup.
- Works fully **offline** using your own order history data.

**Algorithm (simple, no external API):**

```python
# menu/ai_recommendations.py

from orders.models import OrderItem
from itertools import combinations
from collections import Counter

def get_frequently_bought_together(menu_item_id, top_n=3):
    """Find items most often ordered in the same order."""
    
    # Find all orders that contain this item
    order_ids = OrderItem.objects.filter(
        menu_item_id=menu_item_id
    ).values_list("order_id", flat=True)
    
    # Get all other items in those orders
    pairs = OrderItem.objects.filter(
        order_id__in=order_ids
    ).exclude(menu_item_id=menu_item_id)
    
    counter = Counter(pairs.values_list("menu_item_id", flat=True))
    return counter.most_common(top_n)
```

**No API key needed!** Runs entirely on your existing data.

---

### 5. 🖼️ Vision AI — Receipt & Complaint Scanner
**Provider**: Google Vision API, Gemini Vision

**What it does:**
- A customer sends a WhatsApp photo of their bill saying "this is wrong". The manager scans it and Gemini Vision auto-reads the amounts and compares to the database record.
- **Smart Complaint Detection**: "Identify if the printed receipt matches order #1042 in the database."

---

### 6. 📱 AI Voice Ordering (Futuristic but Real)
**Provider**: Google Speech-to-Text + Gemini

**What it does:**
- A waiter can speak the order: *"Table 4 wants 2 Butter Chickens and 1 Mango Lassi"*
- AI transcribes → matches menu items → populates the order cart automatically.
- Reduces manual input errors during busy service.

```python
# orders/views.py — new endpoint

def voice_order(request):
    audio = request.FILES["audio"]
    
    # Step 1: Transcribe with Speech-to-Text
    transcript = google_speech.recognize(audio)
    # → "two butter chickens and one mango lassi"
    
    # Step 2: Extract items using Gemini
    prompt = f"""
    Available menu: {list_of_menu_items}
    Extract order from: "{transcript}"
    Return JSON: [{{"name": "Butter Chicken", "qty": 2}}]
    """
    items = gemini.parse(prompt)
    
    # Step 3: Auto-populate cart → send to kitchen
```

---

## 🗓️ Suggested Rollout Plan

| Phase | Feature | Effort | API Needed |
|---|---|---|---|
| **Phase 1** ✅ Done | AI Smart Menu Importer (text) | Low | None |
| **Phase 2** ⭐ Next | Frequently Bought Together (Upsell) | Low | None |
| **Phase 3** | Sales Forecasting + AI Reports Card | Medium | None |
| **Phase 4** | Generative AI Menu Importer (photo/PDF) | Medium | Gemini API |
| **Phase 5** | Manager Chatbot (NL → DB Query) | High | Gemini API |
| **Phase 6** | Voice Ordering | Very High | Gemini + Speech API |

---

## 🔑 API Setup (for Phase 4+)

### Google Gemini
```bash
pip install google-generativeai
```
```python
# .env file (never commit!)
GEMINI_API_KEY=your_key_here
```
Free tier available: **1500 requests/day** on Gemini Flash.

### OpenAI
```bash
pip install openai
```
Pay-per-token. GPT-4o-mini is extremely cheap (~$0.001 per 1000 tokens).

---

## 💡 Quickest Win Right Now (Zero Cost)

> **Implement the "Frequently Bought Together" Upsell Engine.**
> It requires zero external APIs, works on your existing order data,
> and can directly increase your average order value.
>
> Should I build this next? 🚀

---

*Last updated: 2026-03-30 | Fine Dining POS System*
