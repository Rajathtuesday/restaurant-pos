from datetime import timedelta
from django.utils import timezone

def get_business_date(dt=None, outlet=None):
    """
    Returns the business date for a given datetime based on the outlet's
    business day start hour. If no datetime is provided, uses current time.
    """
    if not dt:
        dt = timezone.now()
    
    # Convert to local time
    local_dt = timezone.localtime(dt)
    
    # Get cutoff from outlet, default to 6 AM
    cutoff_hour = 6
    if outlet and hasattr(outlet, 'business_day_start_hour'):
        cutoff_hour = outlet.business_day_start_hour
        
    if local_dt.hour < cutoff_hour:
        return local_dt.date() - timedelta(days=1)
    
    return local_dt.date()
