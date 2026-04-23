from django.http import JsonResponse
from django.db import connection

def health_check(request):
    try:
        # Check database connection
        connection.ensure_connection()
        return JsonResponse({"status": "healthy", "database": "connected"}, status=200)
    except Exception as e:
        return JsonResponse({"status": "unhealthy", "database": "disconnected", "error": str(e)}, status=503)
