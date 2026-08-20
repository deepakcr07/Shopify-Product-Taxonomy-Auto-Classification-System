"""
Django Web Views for Dashboard and Review Interface.
"""
from django.shortcuts import render, get_object_or_404
from apps.products.models import Product, ClassificationResult
from apps.jobs.models import BatchJob
from apps.taxonomy.models import TaxonomyCategory


def dashboard_view(request):
    """Renders the main KPI overview and batch control dashboard."""
    total_products = Product.objects.count()
    total_categories = TaxonomyCategory.objects.count()
    latest_job = BatchJob.objects.first()
    recent_classifications = ClassificationResult.objects.select_related(
        'product', 'predicted_category'
    ).exclude(status=ClassificationResult.STATUS_PENDING)[:10]

    context = {
        'total_products': total_products,
        'total_categories': total_categories,
        'latest_job': latest_job,
        'recent_classifications': recent_classifications,
    }
    return render(request, 'dashboard.html', context)


def products_view(request):
    """Renders the interactive product catalogue and review table."""
    total_products = Product.objects.count()
    status_filter = request.GET.get('status', 'all')
    context = {
        'total_products': total_products,
        'current_status': status_filter,
    }
    return render(request, 'products.html', context)


def product_detail_view(request, pk):
    """Renders single product view with full category breadcrumbs and attributes."""
    product = get_object_or_404(
        Product.objects.select_related('classification', 'classification__predicted_category'),
        pk=pk
    )
    context = {
        'product': product,
        'classification': getattr(product, 'classification', None),
        'audit_logs': product.audit_logs.all()[:15],
    }
    return render(request, 'product_detail.html', context)
