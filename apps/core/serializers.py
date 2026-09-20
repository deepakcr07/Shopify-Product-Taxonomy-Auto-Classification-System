from rest_framework import serializers
from apps.taxonomy.models import TaxonomyCategory, TaxonomyAttribute, TaxonomyAttributeValue
from apps.products.models import Product, ClassificationResult, AuditLog
from apps.jobs.models import BatchJob


class TaxonomyCategorySerializer(serializers.ModelSerializer):
    breadcrumbs = serializers.SerializerMethodField()

    class Meta:
        model = TaxonomyCategory
        fields = ['id', 'code', 'name', 'full_name', 'level', 'parent_id', 'is_leaf', 'breadcrumbs']

    def get_breadcrumbs(self, obj):
        return obj.get_breadcrumbs()


class ClassificationResultSerializer(serializers.ModelSerializer):
    predicted_category = TaxonomyCategorySerializer(read_only=True)
    approved_category = TaxonomyCategorySerializer(read_only=True)
    effective_category = serializers.SerializerMethodField()
    confidence_percent = serializers.ReadOnlyField()
    shopify_category_attributes = serializers.SerializerMethodField()
    extracted_attributes = serializers.SerializerMethodField()

    class Meta:
        model = ClassificationResult
        fields = [
            'id', 'predicted_category', 'approved_category', 'effective_category',
            'confidence_score', 'confidence_percent', 'status', 'review_decision',
            'alternatives', 'extracted_attributes', 'shopify_category_attributes',
            'confidence_breakdown', 'image_status', 'taxonomy_version',
            'classifier_version', 'attempt_count', 'last_error', 'reviewed_by',
            'review_notes', 'error_log', 'processed_at', 'reviewed_at'
        ]

    def get_effective_category(self, obj):
        cat = obj.effective_category
        if not cat:
            return None
        return {
            'id': cat.id,
            'name': cat.name,
            'full_name': cat.full_name,
            'code': cat.code,
            'breadcrumbs': cat.get_breadcrumbs()
        }

    def get_shopify_category_attributes(self, obj):
        if obj.product and obj.effective_category:
            from apps.classifier.attribute_extractor import extract_category_attributes
            return extract_category_attributes(obj.product, obj.effective_category)
        attrs = obj.extracted_attributes or {}
        if isinstance(attrs, dict) and 'shopify_category_attributes' in attrs:
            return attrs['shopify_category_attributes']
        return []

    def get_extracted_attributes(self, obj):
        return {
            'shopify_category_attributes': self.get_shopify_category_attributes(obj)
        }


class ProductSerializer(serializers.ModelSerializer):
    classification = ClassificationResultSerializer(read_only=True)

    class Meta:
        model = Product
        fields = [
            'id', 'product_number', 'model_number', 'name', 'brand', 'description',
            'product_category', 'product_sub_category', 'collection_name',
            'color_collection', 'product_color', 'materials', 'bullets',
            'set_includes', 'product_weight', 'product_dimensions', 'assembly_required',
            'is_set', 'country_of_origin', 'item_cost', 'map_price', 'msrp',
            'image_url', 'all_images', 'product_url', 'created_at', 'classification'
        ]


class BatchJobSerializer(serializers.ModelSerializer):
    progress_percentage = serializers.ReadOnlyField()
    duration_seconds = serializers.ReadOnlyField()
    throughput_ips = serializers.ReadOnlyField(source='throughput_items_per_sec')

    class Meta:
        model = BatchJob
        fields = [
            'id', 'name', 'status', 'batch_type', 'total_items', 'processed_items',
            'auto_classified_items', 'needs_review_items', 'failed_items',
            'resumed_count', 'retry_count', 'chunk_size', 'current_chunk',
            'total_chunks', 'progress_percentage', 'duration_seconds',
            'throughput_ips', 'started_at', 'completed_at', 'error_message'
        ]


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = '__all__'
