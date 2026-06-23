"""
Demo helper — fires a correctly-signed fake Zomato/Swiggy order at the
local aggregator webhook so you can show "online order comes in automatically"
without a real Zomato/Swiggy connection.

Run (with the dev server running in another terminal):
    python manage.py shell -c "from scripts.simulate_aggregator_order import run; run()"

Optional args:
    run(source="swiggy")                 # default is "zomato"
    run(base_url="http://127.0.0.1:8000")  # default
"""
import hashlib
import hmac
import json

import requests

from menu.models import MenuItem
from setup.models import AggregatorConfig
from tenants.models import Tenant, Outlet


def run(source="zomato", base_url="http://127.0.0.1:8000"):
    tenant = Tenant.objects.first()
    outlet = Outlet.objects.filter(tenant=tenant).first()

    config, _ = AggregatorConfig.for_outlet(outlet, tenant)
    secret = config.zomato_webhook_secret if source == "zomato" else config.swiggy_webhook_secret
    if not secret:
        secret = "demo-secret-only-for-this-test"
        if source == "zomato":
            config.zomato_webhook_secret = secret
            config.zomato_enabled = True
        else:
            config.swiggy_webhook_secret = secret
            config.swiggy_enabled = True
        config.save()
        print(f"No {source} webhook secret was set — generated a temporary one for this demo.")

    items = list(MenuItem.objects.filter(tenant=tenant, outlet=outlet, is_available=True)[:2])
    if not items:
        print("No available menu items found for this outlet — add one before demoing.")
        return

    payload = {
        "tenant_id": tenant.id,
        "outlet_id": outlet.id,
        "source": source,
        "aggregator_order_id": f"DEMO-{source.upper()}-{hash(json.dumps(items[0].name)) % 100000}",
        "items": [{"menu_item_id": item.id, "quantity": 1} for item in items],
    }
    body = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    url = f"{base_url}/orders/api/aggregator/webhook/?tenant_id={tenant.id}&outlet_id={outlet.id}"
    response = requests.post(
        url, data=body,
        headers={"Content-Type": "application/json", "X-Signature": signature},
    )

    print(f"POST {url}")
    print(f"Items: {', '.join(i.name for i in items)}")
    print(f"Status: {response.status_code}")
    print(response.json())
