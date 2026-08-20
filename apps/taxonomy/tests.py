from django.test import TestCase
from apps.taxonomy.models import TaxonomyCategory, TaxonomyAttribute, TaxonomyAttributeValue


class TaxonomyModelTests(TestCase):
    def setUp(self):
        self.attr = TaxonomyAttribute.objects.create(
            id='gid://shopify/TaxonomyAttribute/1',
            name='Material',
            handle='material'
        )
        self.val = TaxonomyAttributeValue.objects.create(
            id='gid://shopify/TaxonomyValue/1',
            attribute=self.attr,
            name='Wood'
        )
        self.root_cat = TaxonomyCategory.objects.create(
            id='gid://shopify/TaxonomyCategory/fu',
            code='fu',
            name='Furniture',
            full_name='Furniture',
            level=0
        )
        self.leaf_cat = TaxonomyCategory.objects.create(
            id='gid://shopify/TaxonomyCategory/fu-1',
            code='fu-1',
            name='Chairs',
            full_name='Furniture > Chairs',
            level=1,
            parent=self.root_cat
        )

    def test_category_hierarchy_and_breadcrumbs(self):
        self.assertEqual(self.leaf_cat.parent.name, 'Furniture')
        self.assertEqual(self.leaf_cat.get_breadcrumbs(), ['Furniture', 'Chairs'])
        self.assertEqual(self.root_cat.children.count(), 1)

    def test_attribute_value_association(self):
        self.assertEqual(self.val.attribute.name, 'Material')
        self.assertEqual(self.attr.values.count(), 1)
