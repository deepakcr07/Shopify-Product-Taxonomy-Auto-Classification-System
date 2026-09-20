"""
REST API ViewSets and Endpoints for Shopify Taxonomy Auto-Classification System.
Provides high-performance server-side filtering, batch controls, manual review, validation, and export.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from django.db.models import Q, Count, Avg
from django.http import HttpResponse
from django.utils import timezone
from django.shortcuts import get_object_or_404
from apps.products.models import Product, ClassificationResult, AuditLog
from apps.taxonomy.models import TaxonomyCategory
from apps.jobs.models import BatchJob
from apps.jobs.worker import (
    start_batch_classification, pause_batch_job,
    resume_batch_job, retry_failed_jobs, retry_single_product
)
from apps.products.services.importer import import_product_file, validate_product_file
from apps.products.services.exporter import export_classification_data
from apps.core.serializers import (
    ProductSerializer, ClassificationResultSerializer,
    TaxonomyCategorySerializer, BatchJobSerializer
)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100


class DashboardMetricsAPIView(APIView):
    """Provides high-level dashboard metrics, classification status counts, and confidence distributions."""
    def get(self, request):
        total_products = Product.objects.count()
        status_counts = dict(
            ClassificationResult.objects.values('status').annotate(count=Count('id')).values_list('status', 'count')
        )

        pending_count = status_counts.get(ClassificationResult.STATUS_PENDING, 0)
        auto_count = status_counts.get(ClassificationResult.STATUS_AUTO_CLASSIFIED, 0)
        review_count = status_counts.get(ClassificationResult.STATUS_NEEDS_REVIEW, 0)
        approved_count = status_counts.get(ClassificationResult.STATUS_APPROVED, 0)
        rejected_count = status_counts.get(ClassificationResult.STATUS_REJECTED, 0)
        failed_count = status_counts.get(ClassificationResult.STATUS_FAILED, 0)
        processed_count = total_products - pending_count

        avg_conf = ClassificationResult.objects.exclude(status=ClassificationResult.STATUS_PENDING).aggregate(
            avg=Avg('confidence_score')
        )['avg'] or 0.0

        latest_job = BatchJob.objects.first()

        # Category distribution top 8 (using effective category)
        top_categories = list(
            ClassificationResult.objects.exclude(predicted_category__isnull=True)
            .values('predicted_category__name')
            .annotate(count=Count('id'))
            .order_by('-count')[:8]
        )

        return Response({
            'total_products': total_products,
            'processed_count': processed_count,
            'pending_count': pending_count,
            'auto_classified_count': auto_count,
            'needs_review_count': review_count,
            'approved_count': approved_count,
            'rejected_count': rejected_count,
            'failed_count': failed_count,
            'average_confidence': round(avg_conf * 100, 1),
            'latest_job': BatchJobSerializer(latest_job).data if latest_job else None,
            'top_categories': top_categories,
        })


class ProductListAPIView(APIView):
    """Lists products with server-side database search, status filtering, category filtering, and sorting."""
    def get(self, request):
        qs = Product.objects.select_related(
            'classification',
            'classification__predicted_category',
            'classification__approved_category'
        ).all()

        # Search query (server-side indexed SQL)
        query = request.GET.get('q', '').strip()
        if query:
            qs = qs.filter(
                Q(name__icontains=query) |
                Q(product_number__icontains=query) |
                Q(model_number__icontains=query) |
                Q(brand__icontains=query) |
                Q(materials__icontains=query) |
                Q(product_category__icontains=query) |
                Q(product_sub_category__icontains=query)
            )

        # Status filter
        status_filter = request.GET.get('status', '').strip()
        if status_filter and status_filter != 'all':
            qs = qs.filter(classification__status=status_filter)

        # Min confidence filter
        min_conf = request.GET.get('min_confidence')
        if min_conf is not None and min_conf != '':
            try:
                val = float(min_conf)
                qs = qs.filter(classification__confidence_score__gte=val / 100.0 if val > 1 else val)
            except ValueError:
                pass

        # Image status filter
        img_filter = request.GET.get('image_status')
        if img_filter and img_filter != 'all':
            qs = qs.filter(classification__image_status=img_filter)

        # Ordering
        ordering = request.GET.get('ordering', '-id')
        if ordering == 'confidence_desc':
            qs = qs.order_by('-classification__confidence_score')
        elif ordering == 'confidence_asc':
            qs = qs.order_by('classification__confidence_score')
        elif ordering == 'name_asc':
            qs = qs.order_by('name')
        else:
            qs = qs.order_by('-id')

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = ProductSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class ProductDetailAPIView(APIView):
    """Returns single product details, classification, alternative categories, and audit logs."""
    def get(self, request, pk):
        product = get_object_or_404(
            Product.objects.select_related(
                'classification',
                'classification__predicted_category',
                'classification__approved_category'
            ),
            pk=pk
        )
        data = ProductSerializer(product).data
        data['audit_logs'] = list(product.audit_logs.values('action', 'old_value', 'new_value', 'performed_by', 'timestamp')[:20])
        return Response(data)


class ApproveProductAPIView(APIView):
    """Approves the current predicted category for a product."""
    def post(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        result = product.classification
        reviewer = request.data.get('reviewer', 'Reviewer')
        notes = request.data.get('notes', '')

        old_status = result.status
        result.approved_category = result.predicted_category
        result.status = ClassificationResult.STATUS_APPROVED
        result.review_decision = ClassificationResult.DECISION_HUMAN_APPROVED
        result.reviewed_by = reviewer
        result.review_notes = notes
        result.reviewed_at = timezone.now()
        result.save()

        AuditLog.objects.create(
            product=product,
            action='approved',
            old_value={'status': old_status},
            new_value={
                'status': result.status,
                'category': result.effective_category_name,
                'decision': result.review_decision
            },
            performed_by=reviewer
        )

        return Response({
            'success': True,
            'message': f"Product {product.product_number} approved successfully.",
            'classification': ClassificationResultSerializer(result).data
        })


class UpdateProductCategoryAPIView(APIView):
    """Updates and human-overrides the assigned Shopify taxonomy category for a product."""
    def post(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        result = product.classification
        category_id = request.data.get('category_id')
        reviewer = request.data.get('reviewer', 'Reviewer')
        notes = request.data.get('notes', '')

        if not category_id:
            return Response({'error': 'category_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        category = get_object_or_404(TaxonomyCategory, pk=category_id)
        old_cat_name = result.effective_category_name

        result.approved_category = category
        result.status = ClassificationResult.STATUS_APPROVED
        result.review_decision = ClassificationResult.DECISION_HUMAN_OVERRIDDEN
        result.confidence_score = 1.0  # Manually verified
        result.reviewed_by = reviewer
        result.review_notes = notes
        result.reviewed_at = timezone.now()

        # Re-extract category-relevant attributes for the newly selected category
        from apps.classifier.attribute_extractor import extract_attributes
        result.extracted_attributes = extract_attributes(product, category)
        result.save()

        AuditLog.objects.create(
            product=product,
            action='category_changed',
            old_value={'category': old_cat_name},
            new_value={
                'category': category.full_name,
                'category_id': category.id,
                'decision': result.review_decision
            },
            performed_by=reviewer
        )

        return Response({
            'success': True,
            'message': f"Category updated to '{category.name}'",
            'classification': ClassificationResultSerializer(result).data
        })


class ProductCategoryAttributesAPIView(APIView):
    """Returns category-relevant attributes & values for a product given any selected Shopify category."""
    def get(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        category_id = request.GET.get('category_id')
        if category_id:
            category = get_object_or_404(TaxonomyCategory, pk=category_id)
        else:
            category = product.classification.effective_category if hasattr(product, 'classification') else None

        from apps.classifier.attribute_extractor import extract_category_attributes
        attrs = extract_category_attributes(product, category)
        return Response({
            'product_id': product.id,
            'category_id': category.id if category else None,
            'category_name': category.name if category else None,
            'category_path': category.full_name if category else None,
            'shopify_category_attributes': attrs,
        })


class UpdateProductAttributesAPIView(APIView):
    """Updates or adds custom attribute values for a product."""
    def post(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        result = product.classification
        attributes = request.data.get('attributes', {})
        reviewer = request.data.get('reviewer', 'Reviewer')

        old_attrs = result.extracted_attributes or {}
        result.extracted_attributes = attributes
        result.save()

        AuditLog.objects.create(
            product=product,
            action='attributes_updated',
            old_value=old_attrs,
            new_value=attributes,
            performed_by=reviewer
        )

        return Response({
            'success': True,
            'message': 'Attributes updated successfully',
            'extracted_attributes': result.extracted_attributes
        })


class RetryProductAPIView(APIView):
    """Retries classification for a single product."""
    def post(self, request, pk):
        check_image = bool(request.data.get('check_image', True))
        try:
            res = retry_single_product(pk, check_image=check_image)
            return Response({
                'success': True,
                'message': f"Product #{pk} classification retried.",
                'classification': ClassificationResultSerializer(res).data
            })
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class StartJobAPIView(APIView):
    """Triggers or starts a batch classification background process."""
    def post(self, request):
        chunk_size = int(request.data.get('chunk_size', 100))
        check_images = bool(request.data.get('check_images', False))
        retry_failed = bool(request.data.get('retry_failed', False))

        job = start_batch_classification(
            chunk_size=chunk_size,
            check_images=check_images,
            retry_failed=retry_failed
        )
        return Response({
            'success': True,
            'message': f"Started batch job #{job.id}",
            'job': BatchJobSerializer(job).data
        })


class ListJobsAPIView(APIView):
    """Lists all recent batch jobs with progress and metrics."""
    def get(self, request):
        jobs = BatchJob.objects.all()[:20]
        return Response(BatchJobSerializer(jobs, many=True).data)


class JobStatusAPIView(APIView):
    """Returns current live progress and metrics of a running or recent batch job."""
    def get(self, request, pk=None):
        if pk:
            job = get_object_or_404(BatchJob, pk=pk)
        else:
            job = BatchJob.objects.first()
            if not job:
                return Response({'status': 'idle', 'message': 'No jobs found'})
        return Response(BatchJobSerializer(job).data)


class PauseJobAPIView(APIView):
    """Pauses a running batch job gracefully."""
    def post(self, request, pk):
        pause_batch_job(pk)
        job = get_object_or_404(BatchJob, pk=pk)
        return Response({
            'success': True,
            'message': f"Job #{pk} pause requested.",
            'job': BatchJobSerializer(job).data
        })


class ResumeJobAPIView(APIView):
    """Resumes a paused or stopped batch job."""
    def post(self, request, pk):
        job = resume_batch_job(pk)
        return Response({
            'success': True,
            'message': f"Job #{pk} resumed.",
            'job': BatchJobSerializer(job).data
        })


class RetryJobAPIView(APIView):
    """Retries all failed items from a batch job."""
    def post(self, request, pk=None):
        job = retry_failed_jobs(job_id=pk)
        if not job:
            return Response({
                'success': False,
                'message': "No failed items to retry."
            })
        return Response({
            'success': True,
            'message': f"Retry worker launched for job #{job.id}",
            'job': BatchJobSerializer(job).data
        })


class TaxonomySearchAPIView(APIView):
    """Instant autocomplete search across all Shopify Product Taxonomy categories."""
    def get(self, request):
        q = request.GET.get('q', '').strip()
        if not q or len(q) < 2:
            cats = TaxonomyCategory.objects.filter(level__lte=2)[:20]
        else:
            cats = TaxonomyCategory.objects.filter(
                Q(name__icontains=q) | Q(full_name__icontains=q)
            )[:30]
        return Response(TaxonomyCategorySerializer(cats, many=True).data)


class ValidateImportAPIView(APIView):
    """Inspects a file before importing and returns validation statistics & preview."""
    def post(self, request):
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return Response({'error': 'No file uploaded'}, status=status.HTTP_400_BAD_REQUEST)

        summary = validate_product_file(uploaded_file)
        return Response(summary)


class ImportAPIView(APIView):
    """Uploads and ingests a product catalogue Excel or CSV file idempotently."""
    def post(self, request):
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return Response({'error': 'No file uploaded'}, status=status.HTTP_400_BAD_REQUEST)

        result = import_product_file(uploaded_file)
        return Response(result)


from django.views import View

class ExportAPIView(View):
    """Exports classified product data into CSV, XLSX, or JSON with approved/predicted separation."""
    def get(self, request, *args, **kwargs):
        export_format = request.GET.get('format', 'csv').lower()
        status_filter = request.GET.get('status')
        min_conf = request.GET.get('min_confidence')

        content, content_type, filename = export_classification_data(
            export_format=export_format,
            status_filter=status_filter,
            min_confidence=min_conf
        )

        response = HttpResponse(content, content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
