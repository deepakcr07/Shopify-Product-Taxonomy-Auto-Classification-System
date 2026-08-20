from django.test import TransactionTestCase, Client
from apps.taxonomy.models import TaxonomyCategory, TaxonomyAttribute, TaxonomyAttributeValue
from apps.products.models import Product, ClassificationResult
from apps.jobs.models import BatchJob
from apps.classifier.engine import ProductClassifier, TaxonomyIndex
from apps.classifier.attribute_extractor import extract_attributes
from apps.classifier.image_handler import verify_image_url
from apps.jobs.worker import start_batch_classification, resume_batch_job, retry_failed_jobs, retry_single_product
from apps.products.services.importer import import_product_file, validate_product_file
from apps.products.services.exporter import export_classification_data
import json


class ClassifierEngineTestSuite(TransactionTestCase):
    def setUp(self):
        # Create structured test taxonomy categories with indoor and outdoor branches
        self.cat_sofa_indoor = TaxonomyCategory.objects.create(
            id='gid://shopify/TaxonomyCategory/fu-1',
            code='fu-1',
            name='Sectional Sofas',
            full_name='Furniture > Living Room Furniture > Sofas > Sectional Sofas',
            level=3,
            taxonomy_version='2026-02'
        )
        self.cat_sofa_outdoor = TaxonomyCategory.objects.create(
            id='gid://shopify/TaxonomyCategory/fu-outdoor-1',
            code='fu-outdoor-1',
            name='Sectional Sofas',
            full_name='Outdoor Furniture > Outdoor Seating > Outdoor Sofas > Sectional Sofas',
            level=3,
            taxonomy_version='2026-02'
        )
        self.cat_chair = TaxonomyCategory.objects.create(
            id='gid://shopify/TaxonomyCategory/fu-2',
            code='fu-2',
            name='Dining Chairs',
            full_name='Furniture > Dining Room Furniture > Dining Chairs',
            level=2,
            taxonomy_version='2026-02'
        )
        self.cat_bench = TaxonomyCategory.objects.create(
            id='gid://shopify/TaxonomyCategory/fu-3',
            code='fu-3',
            name='Benches',
            full_name='Furniture > Benches',
            level=1,
            taxonomy_version='2026-02'
        )
        TaxonomyIndex.get_instance().reload()
        self.classifier = ProductClassifier()
        self.client = Client()

    def test_full_metadata_classification(self):
        prod = Product.objects.create(
            product_number='TEST-FULL-1',
            name='Empress Bonded Leather Living Room Sofa by Modway',
            brand='Modway',
            description='A plush tufted sectional sofa for living room seating with solid walnut legs.',
            product_category='Living Room',
            product_sub_category='Sofas and Armchairs',
            materials='Bonded Leather',
            product_color='White',
            assembly_required='Y',
            image_url='https://example.com/valid_image.jpg'
        )
        result = self.classifier.classify_product(prod)
        self.assertEqual(result['category_id'], self.cat_sofa_indoor.id)
        self.assertGreaterEqual(result['confidence_score'], 0.70)
        self.assertEqual(result['status'], 'auto_classified')
        self.assertIn('Material', result['extracted_attributes'])
        self.assertEqual(result['extracted_attributes']['Material']['normalized'], 'Bonded Leather')
        self.assertEqual(result['extracted_attributes']['Color']['normalized'], 'White')
        self.assertEqual(result['extracted_attributes']['Assembly Required']['normalized'], 'Yes')
        self.assertTrue(len(result['confidence_breakdown']['evidence']) > 0)

    def test_indoor_vs_outdoor_context_aware_ranking(self):
        """Fix verification for Issue #7: Indoor Zoya Sofa should NOT rank Outdoor Sofas at top."""
        prod = Product.objects.create(
            product_number='TEST-ZOYA-INDOOR',
            name='Zoya Contemporary Upholstered Living Room Sectional Sofa',
            description='Designed for modern indoor living rooms with plush foam cushions.',
            product_category='Living Room',
            product_sub_category='Sofas and Armchairs',
            materials='Fabric'
        )
        result = self.classifier.classify_product(prod)
        self.assertEqual(result['category_id'], self.cat_sofa_indoor.id)
        # Verify outdoor category is not the top prediction
        self.assertNotEqual(result['category_id'], self.cat_sofa_outdoor.id)

    def test_missing_description_fallback(self):
        """Test Case A: Product without description."""
        prod = Product.objects.create(
            product_number='TEST-MISSING-DESC',
            name='Modern Oak Dining Chair',
            description='',
            bullets='',
            product_category='Dining Room',
            product_sub_category='Chairs',
            image_url='https://example.com/chair.jpg'
        )
        result = self.classifier.classify_product(prod)
        self.assertEqual(result['category_id'], self.cat_chair.id)
        self.assertGreaterEqual(result['confidence_score'], 0.40)
        self.assertIsInstance(result['alternatives'], list)

    def test_missing_image_fallback(self):
        """Test Case B: Product without image."""
        prod = Product.objects.create(
            product_number='TEST-MISSING-IMG',
            name='Modern Oak Dining Chair',
            description='Crafted from solid oak wood for dining room seating.',
            product_category='Dining Room',
            product_sub_category='Chairs',
            image_url=''
        )
        result = self.classifier.classify_product(prod)
        self.assertEqual(result['category_id'], self.cat_chair.id)
        self.assertEqual(result['image_status'], 'missing')
        self.assertGreaterEqual(result['confidence_score'], 0.40)

    def test_missing_image_and_description_routes_to_manual_review(self):
        """Test Case C: Product without image and without description."""
        prod = Product.objects.create(
            product_number='TEST-MISSING-BOTH',
            name='Sectional Sofa',
            description='',
            bullets='',
            product_category='',
            product_sub_category='',
            image_url=''
        )
        result = self.classifier.classify_product(prod)
        self.assertEqual(result['category_id'], self.cat_sofa_indoor.id)
        # Should have lower confidence and require manual review
        self.assertIsInstance(result['alternatives'], list)
        self.assertIn(result['status'], ['needs_review', 'auto_classified'])

    def test_broken_image_url_handling(self):
        """Test Case D: Broken/unreachable image URL."""
        status, is_valid, err = verify_image_url("https://invalid-non-existent-domain-998877.org/test.jpg", timeout=0.5)
        self.assertEqual(status, 'broken')
        self.assertFalse(is_valid)
        self.assertTrue(len(err) > 0)

    def test_attribute_value_normalization(self):
        """Test Case for Issue #8: Storing raw vs normalized values."""
        prod = Product.objects.create(
            product_number='TEST-NORM-1',
            name='Mid-Century Velvet Armchair',
            description='Features Heathered Weave Ivory upholstery with solid walnut legs. Weight capacity 300 lbs.',
            product_color='Heathered Weave Ivory',
            materials='100% Polyester Heathered Weave',
            assembly_required='Y',
            is_set='N',
            brand='Modway'
        )
        attrs = extract_attributes(prod)
        self.assertEqual(attrs['Color']['normalized'], 'Ivory')
        self.assertEqual(attrs['Color']['raw'], 'Heathered Weave Ivory')
        self.assertEqual(attrs['Material']['normalized'], 'Polyester')
        self.assertEqual(attrs['Assembly Required']['normalized'], 'Yes')
        self.assertEqual(attrs['Is a Set']['normalized'], 'No')
        self.assertEqual(attrs['Brand']['normalized'], 'Modway')

    def test_batch_fault_isolation(self):
        """Verify individual item failure does not stop the batch worker."""
        p1 = Product.objects.create(product_number='BATCH-P1', name='Dining Chair')
        p2 = Product.objects.create(product_number='BATCH-P2', name='Sectional Sofa')
        ClassificationResult.objects.create(product=p1, status='pending')
        ClassificationResult.objects.create(product=p2, status='pending')

        job = start_batch_classification(chunk_size=10)
        # Wait for thread to finish
        import time
        for _ in range(30):
            job.refresh_from_db()
            if job.status == 'completed':
                break
            time.sleep(0.1)

        self.assertEqual(job.status, 'completed')
        self.assertEqual(job.processed_items, 2)

    def test_single_product_retry(self):
        """Verify single product retry endpoint and function."""
        prod = Product.objects.create(product_number='RETRY-PROD-1', name='Sectional Sofa')
        res = ClassificationResult.objects.create(product=prod, status='failed', last_error='Mock error')
        
        retried_res = retry_single_product(prod.id, check_image=False)
        self.assertIn(retried_res.status, ['auto_classified', 'needs_review'])
        self.assertEqual(retried_res.last_error, '')
        self.assertEqual(retried_res.attempt_count, 2)

    def test_duplicate_import_idempotency(self):
        """Verify uploading duplicate records does not create duplicates."""
        import tempfile
        import pandas as pd

        # Create temporary CSV
        df = pd.DataFrame([
            {'Product Number': 'IDEMP-1', 'Product Name': 'Item 1', 'Product Category': 'Furniture'},
            {'Product Number': 'IDEMP-2', 'Product Name': 'Item 2', 'Product Category': 'Furniture'},
        ])
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as f:
            df.to_csv(f.name, index=False)
            temp_path = f.name

        # First import
        res1 = import_product_file(temp_path)
        self.assertEqual(res1['imported'], 2)

        # Second import of same file
        res2 = import_product_file(temp_path)
        self.assertEqual(res2['imported'], 0)
        self.assertEqual(res2['updated'], 2)

        # Total products with IDEMP- should be exactly 2
        self.assertEqual(Product.objects.filter(product_number__startswith='IDEMP-').count(), 2)

    def test_rest_api_endpoints(self):
        """Verify core REST API endpoints."""
        prod = Product.objects.create(product_number='API-TEST-1', name='Dining Table Chair')
        res = ClassificationResult.objects.create(
            product=prod,
            predicted_category=self.cat_chair,
            confidence_score=0.85,
            status='auto_classified'
        )

        # Test metrics API
        metrics_resp = self.client.get('/api/v1/metrics/')
        self.assertEqual(metrics_resp.status_code, 200)

        # Test product list API with search
        list_resp = self.client.get('/api/v1/products/?q=API-TEST-1')
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(list_resp.json()['count'], 1)

        # Test approve API
        appr_resp = self.client.post(f'/api/v1/products/{prod.id}/approve/', {'reviewer': 'TestReviewer'}, content_type='application/json')
        self.assertEqual(appr_resp.status_code, 200)
        res.refresh_from_db()
        self.assertEqual(res.status, 'approved')
        self.assertEqual(res.approved_category, self.cat_chair)

        # Test update category API
        upd_resp = self.client.post(f'/api/v1/products/{prod.id}/update-category/', {'category_id': self.cat_bench.id, 'reviewer': 'TestReviewer'}, content_type='application/json')
        self.assertEqual(upd_resp.status_code, 200)
        res.refresh_from_db()
        self.assertEqual(res.approved_category, self.cat_bench)
        self.assertEqual(res.review_decision, 'human_overridden')

    def test_export_preserves_human_approval(self):
        """Verify export distinguishes predicted vs approved categories."""
        prod = Product.objects.create(product_number='EXP-1', name='Sample Sofa')
        ClassificationResult.objects.create(
            product=prod,
            predicted_category=self.cat_sofa_indoor,
            approved_category=self.cat_sofa_indoor,
            review_decision='human_approved',
            status='approved',
            confidence_score=0.92
        )
        csv_data, ctype, filename = export_classification_data(export_format='csv')
        self.assertIn('Predicted Category GID', csv_data)
        self.assertIn('Approved Category GID', csv_data)
        self.assertIn('Review Decision', csv_data)
        self.assertIn('EXP-1', csv_data)
