import json
import time
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from orders.models import Order
from tenants.models import Outlet

def sse_outlet_stream(request, outlet_id):
    """
    Server-Sent Events stream for a specific outlet.
    Pushes updates whenever table states or kitchen orders change.
    """
    def event_stream():
        outlet = get_object_or_404(Outlet, id=outlet_id)
        last_hash = None
        
        while True:
            # Simple implementation: check for most recent order update time
            # In a full production app, you'd use a redis-backed pub/sub or signals
            recent_orders = Order.objects.filter(outlet=outlet, status="open").order_by('-updated_at')[:10]
            
            # Create a fingerprint of current state
            current_state = {
                "order_count": recent_orders.count(),
                "latest_update": str(recent_orders[0].updated_at) if recent_orders.exists() else ""
            }
            current_hash = hash(json.dumps(current_state))
            
            if current_hash != last_hash:
                yield f"data: {json.dumps({'reload': True, 'state': current_state})}\n\n"
                last_hash = current_hash
            
            time.sleep(2) # Check every 2 seconds

    return StreamingHttpResponse(event_stream(), content_type='text/event-stream')
