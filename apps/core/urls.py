from django.urls import path
from apps.core import views, api_views

urlpatterns = [
    # Web UI Pages
    path('', views.dashboard_view, name='dashboard'),
    path('products/', views.products_view, name='products'),
    path('products/<int:pk>/', views.product_detail_view, name='product_detail'),

    # REST APIs - Metrics & Products
    path('api/v1/metrics/', api_views.DashboardMetricsAPIView.as_view(), name='api_metrics'),
    path('api/v1/products/', api_views.ProductListAPIView.as_view(), name='api_product_list'),
    path('api/v1/products/<int:pk>/', api_views.ProductDetailAPIView.as_view(), name='api_product_detail'),
    path('api/v1/products/<int:pk>/approve/', api_views.ApproveProductAPIView.as_view(), name='api_product_approve'),
    path('api/v1/products/<int:pk>/update-category/', api_views.UpdateProductCategoryAPIView.as_view(), name='api_product_update_category'),
    path('api/v1/products/<int:pk>/update-attributes/', api_views.UpdateProductAttributesAPIView.as_view(), name='api_product_update_attributes'),
    path('api/v1/products/<int:pk>/retry/', api_views.RetryProductAPIView.as_view(), name='api_product_retry'),

    # Background Batch Job Control
    path('api/v1/jobs/', api_views.ListJobsAPIView.as_view(), name='api_jobs_list'),
    path('api/v1/jobs/start/', api_views.StartJobAPIView.as_view(), name='api_job_start'),
    path('api/v1/jobs/status/', api_views.JobStatusAPIView.as_view(), name='api_job_status_latest'),
    path('api/v1/jobs/<int:pk>/status/', api_views.JobStatusAPIView.as_view(), name='api_job_status'),
    path('api/v1/jobs/<int:pk>/pause/', api_views.PauseJobAPIView.as_view(), name='api_job_pause'),
    path('api/v1/jobs/<int:pk>/resume/', api_views.ResumeJobAPIView.as_view(), name='api_job_resume'),
    path('api/v1/jobs/retry/', api_views.RetryJobAPIView.as_view(), name='api_jobs_retry_all'),
    path('api/v1/jobs/<int:pk>/retry/', api_views.RetryJobAPIView.as_view(), name='api_job_retry'),

    # Taxonomy & Ingestion/Export
    path('api/v1/taxonomy/search/', api_views.TaxonomySearchAPIView.as_view(), name='api_taxonomy_search'),
    path('api/v1/import/validate/', api_views.ValidateImportAPIView.as_view(), name='api_import_validate'),
    path('api/v1/import/', api_views.ImportAPIView.as_view(), name='api_import'),
    path('api/v1/export/', api_views.ExportAPIView.as_view(), name='api_export'),
]
