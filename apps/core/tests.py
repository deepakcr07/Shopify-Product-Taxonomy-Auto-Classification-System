from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from apps.taxonomy.models import TaxonomyCategory
from apps.products.models import Product, ClassificationResult
from apps.classifier.engine import TaxonomyIndex


class APITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.cat = TaxonomyCategory.objects.create(
            id='gid://shopify/TaxonomyCategory/fu-1',
            code='fu-1',
            name='Dining Chairs',
            full_name='Furniture > Chairs > Dining Chairs',
            level=2
        )
        self.alt_cat = TaxonomyCategory.objects.create(
            id='gid://shopify/TaxonomyCategory/fu-2',
            code='fu-2',
            name='Office Chairs',
            full_name='Furniture > Office Chairs',
            level=1
        )
        TaxonomyIndex.get_instance().reload()

        self.prod = Product.objects.create(
            product_number='TEST-SKU-100',
            name='Velvet Dining Chair',
            materials='Velvet',
            product_color='Blue'
        )
        self.classification = ClassificationResult.objects.create(
            product=self.prod,
            predicted_category=self.cat,
            confidence_score=0.88,
            status=ClassificationResult.STATUS_AUTO_CLASSIFIED,
            extracted_attributes={'Material': 'Velvet', 'Color': 'Blue'}
        )

    def test_metrics_api(self):
        resp = self.client.get('/api/v1/metrics/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['total_products'], 1)
        self.assertEqual(data['auto_classified_count'], 1)

    def test_product_list_api(self):
        resp = self.client.get('/api/v1/products/?q=Velvet')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['product_number'], 'TEST-SKU-100')

    def test_product_detail_api(self):
        resp = self.client.get(f'/api/v1/products/{self.prod.id}/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['name'], 'Velvet Dining Chair')
        self.assertEqual(data['classification']['predicted_category']['name'], 'Dining Chairs')
        self.assertIn('shopify_category_attributes', data['classification'])
        self.assertNotIn('product_specifications', data['classification'])

    def test_category_attributes_preview_api(self):
        resp = self.client.get(f'/api/v1/products/{self.prod.id}/category-attributes/?category_id={self.alt_cat.id}')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['category_id'], self.alt_cat.id)
        self.assertIn('shopify_category_attributes', data)
        self.assertNotIn('product_specifications', data)

    def test_approve_product_api(self):
        resp = self.client.post(f'/api/v1/products/{self.prod.id}/approve/', {'reviewer': 'TestAdmin'})
        self.assertEqual(resp.status_code, 200)
        self.classification.refresh_from_db()
        self.assertEqual(self.classification.status, ClassificationResult.STATUS_APPROVED)
        self.assertEqual(self.classification.reviewed_by, 'TestAdmin')

    def test_update_category_api(self):
        resp = self.client.post(
            f'/api/v1/products/{self.prod.id}/update-category/',
            {'category_id': self.alt_cat.id, 'reviewer': 'Admin'}
        )
        self.assertEqual(resp.status_code, 200)
        self.classification.refresh_from_db()
        self.assertEqual(self.classification.approved_category_id, self.alt_cat.id)
        self.assertEqual(self.classification.effective_category.id, self.alt_cat.id)
        self.assertEqual(self.classification.status, ClassificationResult.STATUS_APPROVED)
        # Check re-extracted category attributes
        data = resp.json()
        self.assertIn('shopify_category_attributes', data['classification'])

    def test_taxonomy_search_api(self):
        resp = self.client.get('/api/v1/taxonomy/search/?q=Dining')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreaterEqual(len(data), 1)
        self.assertEqual(data[0]['name'], 'Dining Chairs')

    def test_export_api(self):
        resp = self.client.get('/api/v1/export/?format=csv')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/csv', resp['Content-Type'])
        self.assertIn('TEST-SKU-100', resp.content.decode('utf-8'))
        self.assertIn('Shopify Category Attributes', resp.content.decode('utf-8'))
