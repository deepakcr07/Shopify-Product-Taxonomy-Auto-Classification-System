from django.db import models
from apps.taxonomy.models import TaxonomyCategory


class Product(models.Model):
    """Stores imported catalogue product records with full attributes, text descriptions, and imagery."""
    product_number = models.CharField(max_length=128, unique=True, db_index=True)
    model_number = models.CharField(max_length=128, blank=True, default='')
    name = models.CharField(max_length=512, db_index=True)
    brand = models.CharField(max_length=255, blank=True, default='', db_index=True)
    description = models.TextField(blank=True, default='')
    product_category = models.CharField(max_length=255, blank=True, default='', db_index=True)
    product_sub_category = models.CharField(max_length=255, blank=True, default='')
    collection_name = models.CharField(max_length=255, blank=True, default='')
    color_collection = models.CharField(max_length=128, blank=True, default='')
    product_color = models.CharField(max_length=128, blank=True, default='')
    materials = models.TextField(blank=True, default='')
    bullets = models.TextField(blank=True, default='')
    set_includes = models.TextField(blank=True, default='')
    product_weight = models.CharField(max_length=64, blank=True, default='')
    product_dimensions = models.TextField(blank=True, default='')
    assembly_required = models.CharField(max_length=32, blank=True, default='')
    is_set = models.CharField(max_length=32, blank=True, default='')
    country_of_origin = models.CharField(max_length=128, blank=True, default='')
    item_cost = models.FloatField(null=True, blank=True)
    map_price = models.FloatField(null=True, blank=True)
    msrp = models.FloatField(null=True, blank=True)
    image_url = models.TextField(blank=True, default='')
    all_images = models.JSONField(default=list, blank=True)
    product_url = models.TextField(blank=True, default='')
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']
        indexes = [
            models.Index(fields=['product_number']),
            models.Index(fields=['name']),
            models.Index(fields=['brand']),
            models.Index(fields=['product_category']),
        ]

    def __str__(self):
        return f"{self.product_number} - {self.name[:50]}"

    def get_clean_text(self):
        """Combines all relevant text features for classification matching."""
        parts = [
            self.name,
            self.brand,
            self.product_sub_category,
            self.product_category,
            self.materials,
            self.bullets,
            self.description
        ]
        return " ".join([p for p in parts if p]).strip()


class ClassificationResult(models.Model):
    """
    Stores the output of the classification engine for a given Product,
    including the predicted Shopify taxonomy category, approved category,
    confidence score, alternatives, extracted attributes, and manual review status.
    """
    STATUS_PENDING = 'pending'
    STATUS_AUTO_CLASSIFIED = 'auto_classified'
    STATUS_NEEDS_REVIEW = 'needs_review'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_AUTO_CLASSIFIED, 'Auto Classified'),
        (STATUS_NEEDS_REVIEW, 'Needs Review'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
        (STATUS_FAILED, 'Failed'),
    ]

    DECISION_AUTO = 'auto_classified'
    DECISION_HUMAN_APPROVED = 'human_approved'
    DECISION_HUMAN_OVERRIDDEN = 'human_overridden'
    DECISION_REJECTED = 'rejected'

    DECISION_CHOICES = [
        (DECISION_AUTO, 'Auto Classified'),
        (DECISION_HUMAN_APPROVED, 'Human Approved'),
        (DECISION_HUMAN_OVERRIDDEN, 'Human Overridden'),
        (DECISION_REJECTED, 'Rejected'),
    ]

    IMAGE_AVAILABLE = 'available'
    IMAGE_MISSING = 'missing'
    IMAGE_BROKEN = 'broken'
    IMAGE_UNCHECKED = 'unchecked'

    IMAGE_STATUS_CHOICES = [
        (IMAGE_AVAILABLE, 'Available'),
        (IMAGE_MISSING, 'Missing'),
        (IMAGE_BROKEN, 'Broken / Unreachable'),
        (IMAGE_UNCHECKED, 'Unchecked'),
    ]

    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name='classification')
    predicted_category = models.ForeignKey(
        TaxonomyCategory,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='predicted_products'
    )
    approved_category = models.ForeignKey(
        TaxonomyCategory,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='approved_products'
    )
    confidence_score = models.FloatField(default=0.0, db_index=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    review_decision = models.CharField(max_length=32, choices=DECISION_CHOICES, default=DECISION_AUTO, blank=True, db_index=True)
    alternatives = models.JSONField(default=list, blank=True)
    extracted_attributes = models.JSONField(default=dict, blank=True)
    confidence_breakdown = models.JSONField(default=dict, blank=True)
    image_status = models.CharField(max_length=32, choices=IMAGE_STATUS_CHOICES, default=IMAGE_UNCHECKED)
    taxonomy_version = models.CharField(max_length=32, default='2026-02', blank=True, db_index=True)
    classifier_version = models.CharField(max_length=32, default='v1.2.0', blank=True)
    attempt_count = models.PositiveIntegerField(default=1)
    last_error = models.TextField(blank=True, default='')
    reviewed_by = models.CharField(max_length=128, blank=True, default='')
    review_notes = models.TextField(blank=True, default='')
    error_log = models.TextField(blank=True, default='')
    processed_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-confidence_score']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['confidence_score']),
            models.Index(fields=['image_status']),
            models.Index(fields=['review_decision']),
            models.Index(fields=['taxonomy_version']),
        ]

    def __str__(self):
        cat_name = self.effective_category_name
        return f"{self.product.product_number}: {cat_name} ({int(self.confidence_score * 100)}%)"

    @property
    def effective_category(self):
        return self.approved_category or self.predicted_category

    @property
    def effective_category_name(self):
        return self.effective_category.name if self.effective_category else "Unclassified"

    @property
    def effective_category_path(self):
        return self.effective_category.full_name if self.effective_category else "Unclassified"

    @property
    def confidence_percent(self):
        return round(self.confidence_score * 100, 1)

    @property
    def is_review_required(self):
        return self.status == self.STATUS_NEEDS_REVIEW


class AuditLog(models.Model):
    """Maintains audit trail of all manual actions, review approvals, and category edits."""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='audit_logs')
    action = models.CharField(max_length=64)
    old_value = models.JSONField(default=dict, blank=True)
    new_value = models.JSONField(default=dict, blank=True)
    performed_by = models.CharField(max_length=128, default='User')
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.action} on {self.product.product_number} at {self.timestamp}"
