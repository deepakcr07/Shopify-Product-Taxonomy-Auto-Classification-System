from django.db import models


class TaxonomyAttribute(models.Model):
    """Represents a standardized Shopify product attribute (e.g., Color, Material, Size)."""
    id = models.CharField(max_length=128, primary_key=True)
    name = models.CharField(max_length=255, db_index=True)
    handle = models.CharField(max_length=255, blank=True, default='')
    description = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
        ]

    def __str__(self):
        return self.name


class TaxonomyAttributeValue(models.Model):
    """Represents a valid predefined value for a Shopify attribute (e.g., Wood, Leather for Material)."""
    id = models.CharField(max_length=128, primary_key=True)
    attribute = models.ForeignKey(TaxonomyAttribute, on_delete=models.CASCADE, related_name='values')
    name = models.CharField(max_length=255, db_index=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['attribute', 'name']),
        ]

    def __str__(self):
        return f"{self.attribute.name}: {self.name}"


class TaxonomyCategory(models.Model):
    """
    Represents a Shopify Product Taxonomy Category.
    Supports hierarchical traversal, full path resolution, and category-level attribute associations.
    """
    id = models.CharField(max_length=128, primary_key=True)
    code = models.CharField(max_length=64, blank=True, default='', db_index=True)
    name = models.CharField(max_length=255, db_index=True)
    full_name = models.CharField(max_length=500, db_index=True)
    level = models.PositiveSmallIntegerField(default=0)
    parent = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='children'
    )
    is_leaf = models.BooleanField(default=True)
    taxonomy_version = models.CharField(max_length=32, default='2026-02', blank=True, db_index=True)
    attributes = models.ManyToManyField(
        TaxonomyAttribute,
        blank=True,
        related_name='categories'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['full_name']
        verbose_name_plural = 'Taxonomy Categories'
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['full_name']),
            models.Index(fields=['level']),
            models.Index(fields=['taxonomy_version']),
        ]

    def __str__(self):
        return self.full_name

    def get_breadcrumbs(self):
        """Returns the hierarchical path as a list of category names."""
        return [part.strip() for part in self.full_name.split('>')]

    def to_dict(self, include_attributes=False):
        data = {
            'id': self.id,
            'code': self.code,
            'name': self.name,
            'full_name': self.full_name,
            'level': self.level,
            'parent_id': self.parent_id,
            'is_leaf': self.is_leaf,
            'breadcrumbs': self.get_breadcrumbs()
        }
        if include_attributes:
            data['attributes'] = [
                {
                    'id': attr.id,
                    'name': attr.name,
                    'handle': attr.handle,
                    'sample_values': list(attr.values.values_list('name', flat=True)[:10])
                }
                for attr in self.attributes.all()
            ]
        return data
