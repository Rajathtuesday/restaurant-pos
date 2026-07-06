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
    
    # Get all users with agent role, with client count + revenue computed
    # in a single annotated query instead of two extra queries per agent.
    agents = User.objects.filter(role='agent').annotate(
        client_count=Count('tenants_sold', distinct=True),
        revenue=Sum(
            'tenants_sold__order__grand_total',
            filter=Q(tenants_sold__order__status__in=['closed', 'paid']),
        ),
    )

    agent_stats = [
        {
            'id': agent.id,
            'agent': agent.username,
            'full_name': f"{agent.first_name} {agent.last_name}" if agent.first_name else agent.username,
            'client_count': agent.client_count,
            'revenue': agent.revenue or 0,
        }
        for agent in agents
    ]

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

    agents = User.objects.filter(role='agent').annotate(
        client_count=Count('tenants_sold', distinct=True),
        revenue=Sum(
            'tenants_sold__order__grand_total',
            filter=Q(tenants_sold__order__status='paid'),
        ),
    )
    data = [
        {
            "agent": agent.username,
            "clients": agent.client_count,
            "revenue": float(agent.revenue or 0),
        }
        for agent in agents
    ]

    return JsonResponse({"status": "success", "data": data})
