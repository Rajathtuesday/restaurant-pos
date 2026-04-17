# agency/views.py
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from tenants.models import Tenant
from accounts.models import User
from django.db.models import Sum, Count, Q
from orders.models import Order
from django.utils.timezone import now, timedelta

@login_required
def agency_performance_dashboard(request):
    """
    Superuser-only view to see how different sales agents/partners are performing.
    """
    if not request.user.is_superuser:
        return HttpResponseForbidden("Only administrators can view this report.")
    
    # Get all users with agent role
    agents = User.objects.filter(role='agent')
    
    agent_stats = []
    for agent in agents:
        clients = Tenant.objects.filter(sales_agent=agent)
        client_count = clients.count()
        
        # Calculate revenue from those clients across all time
        rev_data = Order.objects.filter(
            tenant__in=clients,
            status__in=['closed', 'paid']
        ).aggregate(total=Sum('grand_total'))
        
        total_revenue = rev_data['total'] or 0
        
        agent_stats.append({
            'id': agent.id,
            'agent': agent.username,
            'full_name': f"{agent.first_name} {agent.last_name}" if agent.first_name else agent.username,
            'client_count': client_count,
            'revenue': total_revenue,
        })

    # Sort by revenue descending
    agent_stats = sorted(agent_stats, key=lambda x: x['revenue'], reverse=True)

    # General High-level Metrics
    total_tenants = Tenant.objects.count()
    unassigned_tenants = Tenant.objects.filter(sales_agent__isnull=True).count()
    
    total_global_revenue = Order.objects.filter(
        status__in=['closed', 'paid']
    ).aggregate(total=Sum('grand_total'))['total'] or 0

    return render(request, 'agency/dashboard.html', {
        'agent_stats': agent_stats,
        'total_tenants': total_tenants,
        'unassigned_tenants': unassigned_tenants,
        'total_global_revenue': total_global_revenue,
        'overall_revenue': total_global_revenue,
    })

@login_required
def agency_stats_api(request):
    """
    JSON API for pulling stats for charts or external integrations.
    """
    if not request.user.is_superuser:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    agents = User.objects.filter(role='agent')
    data = []
    for agent in agents:
        clients = Tenant.objects.filter(sales_agent=agent)
        rev = Order.objects.filter(tenant__in=clients, status='paid').aggregate(t=Sum('grand_total'))['t'] or 0
        data.append({
            "agent": agent.username,
            "clients": clients.count(),
            "revenue": float(rev)
        })
    
    return JsonResponse({"status": "success", "data": data})
