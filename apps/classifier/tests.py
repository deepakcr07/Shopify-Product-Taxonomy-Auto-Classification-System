from django.test import TransactionTestCase, Client
from apps.taxonomy.models import TaxonomyCategory, TaxonomyAttribute, TaxonomyAttributeValue
from apps.products.models import Product, ClassificationResult
from apps.jobs.models import BatchJob
from apps.classifier.engine import ProductClassifier, TaxonomyIndex
from apps.classifier.attribute_extractor import (
    extract_attributes, extract_category_attributes,
    extract_product_specifications, clear_attribute_cache
)
from apps.classifier.image_handler import verify_image_url
from apps.jobs.worker import start_batch_classification, resume_batch_job, retry_failed_jobs, retry_single_product
from apps.products.services.importer import import_product_file, validate_product_file
from apps.products.services.exporter import export_classification_data
import json


class ClassifierEngineTestSuite(TransactionTestCase):
    def setUp(self):
        clear_attribute_cache()

        # 1. Create Core Taxonomy Attributes & Canonical Values
        self.attr_material = TaxonomyAttribute.objects.create(
            id='gid://shopify/TaxonomyAttribute/4', name='Material', handle='material'
        )
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/4-1', attribute=self.attr_material, name='Bonded Leather')
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/4-2', attribute=self.attr_material, name='Polyester')
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/4-3', attribute=self.attr_material, name='Wood')
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/4-4', attribute=self.attr_material, name='Fabric')

        self.attr_color = TaxonomyAttribute.objects.create(
            id='gid://shopify/TaxonomyAttribute/1', name='Color', handle='color'
        )
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/1-1', attribute=self.attr_color, name='White')
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/1-2', attribute=self.attr_color, name='Ivory')
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/1-3', attribute=self.attr_color, name='Blue')
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/1-4', attribute=self.attr_color, name='Navy')
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/1-5', attribute=self.attr_color, name='Gray')

        self.attr_pattern = TaxonomyAttribute.objects.create(
            id='gid://shopify/TaxonomyAttribute/3', name='Pattern', handle='pattern'
        )
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/3-1', attribute=self.attr_pattern, name='Solid')
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/3-2', attribute=self.attr_pattern, name='Heathered')

        self.attr_style = TaxonomyAttribute.objects.create(
            id='gid://shopify/TaxonomyAttribute/1351', name='Style', handle='style'
        )
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/1351-1', attribute=self.attr_style, name='Modern')
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/1351-2', attribute=self.attr_style, name='Contemporary')

        self.attr_sec_shape = TaxonomyAttribute.objects.create(
            id='gid://shopify/TaxonomyAttribute/6959', name='Sectional shape', handle='sectional_shape'
        )
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/6959-1', attribute=self.attr_sec_shape, name='L-shaped')
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/6959-2', attribute=self.attr_sec_shape, name='U-shaped')

        self.attr_sec_conf = TaxonomyAttribute.objects.create(
            id='gid://shopify/TaxonomyAttribute/6958', name='Sectional configuration', handle='sectional_configuration'
        )
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/6958-1', attribute=self.attr_sec_conf, name='Corner Unit')

        self.attr_upholstery = TaxonomyAttribute.objects.create(
            id='gid://shopify/TaxonomyAttribute/2797', name='Upholstery material', handle='upholstery_material'
        )
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/2797-1', attribute=self.attr_upholstery, name='Polyester')
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/2797-2', attribute=self.attr_upholstery, name='Leather')

        self.attr_chair_feats = TaxonomyAttribute.objects.create(
            id='gid://shopify/TaxonomyAttribute/3049', name='Chair/Sofa features', handle='chair_sofa_features'
        )
        TaxonomyAttributeValue.objects.create(id='gid://shopify/TaxonomyValue/3049-1', attribute=self.attr_chair_feats, name='Removable cushions')

        # 2. Create structured test taxonomy categories
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

    def test_case_1_sectional_sofas_attributes_association(self):
        """Test Case 1: A product classified as Sectional Sofas receives relevant Shopify attributes."""
        prod = Product.objects.create(
            product_number='TEST-SEC-1',
            name='Zoya 3 Piece Down Filled Overstuffed Sectional Sofa',
            materials='100% Polyester fabric',
            product_color='Heathered Weave Ivory',
            bullets='L-shaped Sectional Sofa\nRemovable Cushions\nSolid Wood Frame',
            description='A luxurious sectional sofa designed for comfort.'
        )
        attrs = extract_attributes(prod, self.cat_sofa_indoor)
        shopify_attrs = attrs['shopify_category_attributes']
        attr_names = [a['attribute_name'] for a in shopify_attrs]

        self.assertIn('Material', attr_names)
        self.assertIn('Color', attr_names)
        self.assertIn('Pattern', attr_names)
        self.assertIn('Sectional shape', attr_names)
        self.assertIn('Chair/Sofa features', attr_names)

        # Verify Sectional shape detected
        sec_shape = next(a for a in shopify_attrs if a['attribute_name'] == 'Sectional shape')
        self.assertEqual(sec_shape['status'], 'detected')
        self.assertEqual(sec_shape['matched_shopify_value'], 'L-shaped')

    def test_case_2_different_category_receives_own_relevant_attributes(self):
        """Test Case 2: A different category receives its own relevant Shopify attributes and not sofa specifics."""
        prod = Product.objects.create(
            product_number='TEST-CHAIR-1',
            name='Modern Oak Dining Chair',
            materials='Solid Wood',
            product_color='Walnut',
            bullets='Solid wooden legs\nComfortable dining chair'
        )
        attrs = extract_attributes(prod, self.cat_chair)
        shopify_attrs = attrs['shopify_category_attributes']
        attr_names = [a['attribute_name'] for a in shopify_attrs]

        # Should receive universal and chair attributes
        self.assertIn('Material', attr_names)
        self.assertIn('Color', attr_names)
        # Should NOT receive sectional sofa-specific attributes
        self.assertNotIn('Sectional configuration', attr_names)
        self.assertNotIn('Sectional shape', attr_names)

    def test_case_3_product_specifications_separated_from_category_attributes(self):
        """Test Case 3: Product specifications are not mixed up with Shopify category attributes."""
        prod = Product.objects.create(
            product_number='TEST-SPECS-1',
            name='Tufted Sofa',
            materials='Polyester',
            assembly_required='Y',
            is_set='N',
            product_weight='105',
            product_dimensions='35.5"L x 84"W x 34.5"H',
            country_of_origin='China',
            brand='Modway'
        )
        attrs = extract_attributes(prod, self.cat_sofa_indoor)
        shopify_attr_names = [a['attribute_name'] for a in attrs['shopify_category_attributes']]

        # Specs should NOT be in shopify category attributes list
        self.assertNotIn('Assembly Required', shopify_attr_names)
        self.assertNotIn('Dimensions', shopify_attr_names)
        self.assertNotIn('Product Weight', shopify_attr_names)
        self.assertNotIn('Country of Origin', shopify_attr_names)
        self.assertNotIn('Brand', shopify_attr_names)
        self.assertNotIn('SHOPIFY_CATEGORY_ATTRIBUTES', shopify_attr_names)
        self.assertNotIn('PRODUCT_SPECIFICATIONS', shopify_attr_names)
        self.assertNotIn('product_specifications', attrs)

    def test_case_4_raw_and_normalized_values_preserved(self):
        """Test Case 4: Raw and normalized attribute values are preserved."""
        prod = Product.objects.create(
            product_number='TEST-RAW-NORM',
            name='Modern Armchair',
            materials='100% Polyester fabric',
            product_color='Heathered Weave Ivory'
        )
        attrs = extract_attributes(prod, self.cat_sofa_indoor)
        shopify_attrs = {a['attribute_name']: a for a in attrs['shopify_category_attributes']}

        mat = shopify_attrs['Material']
        self.assertEqual(mat['raw_value'], '100% Polyester fabric')
        self.assertEqual(mat['normalized_value'], 'Polyester')

        col = shopify_attrs['Color']
        self.assertEqual(col['raw_value'], 'Heathered Weave Ivory')
        self.assertEqual(col['normalized_value'], 'Ivory')

    def test_case_5_matched_shopify_value_canonical(self):
        """Test Case 5: Valid product attribute value matches canonical Shopify taxonomy value."""
        prod = Product.objects.create(
            product_number='TEST-CANON-1',
            name='Sofa',
            materials='Polyester',
            product_color='Navy Fabric'
        )
        attrs = extract_attributes(prod, self.cat_sofa_indoor)
        shopify_attrs = {a['attribute_name']: a for a in attrs['shopify_category_attributes']}

        self.assertEqual(shopify_attrs['Material']['matched_shopify_value'], 'Polyester')
        self.assertEqual(shopify_attrs['Color']['matched_shopify_value'], 'Navy')

    def test_case_6_missing_attribute_values_handled_as_not_detected(self):
        """Test Case 6: Missing attribute values are marked as not_detected without errors."""
        prod = Product.objects.create(
            product_number='TEST-MISSING-ATTR',
            name='Generic Item',
            description=''
        )
        attrs = extract_attributes(prod, self.cat_sofa_indoor)
        shopify_attrs = {a['attribute_name']: a for a in attrs['shopify_category_attributes']}

        # Sectional shape has no mention in product
        sec_shape = shopify_attrs.get('Sectional shape')
        if sec_shape:
            self.assertEqual(sec_shape['status'], 'not_detected')
            self.assertIsNone(sec_shape['raw_value'])
            self.assertIsNone(sec_shape['normalized_value'])
            self.assertIsNone(sec_shape['matched_shopify_value'])

    def test_case_7_missing_description_does_not_break_extraction(self):
        """Test Case 7: Missing description does not break category attribute extraction."""
        prod = Product.objects.create(
            product_number='TEST-NO-DESC',
            name='Empress Bonded Leather Sofa',
            materials='Bonded Leather',
            product_color='White',
            description='',
            bullets=''
        )
        attrs = extract_attributes(prod, self.cat_sofa_indoor)
        self.assertIsInstance(attrs['shopify_category_attributes'], list)
        self.assertNotIn('product_specifications', attrs)
        mat = next(a for a in attrs['shopify_category_attributes'] if a['attribute_name'] == 'Material')
        self.assertEqual(mat['normalized_value'], 'Bonded Leather')

    def test_case_8_missing_image_does_not_break_extraction(self):
        """Test Case 8: Missing image does not break category attribute extraction."""
        prod = Product.objects.create(
            product_number='TEST-NO-IMG',
            name='Empress Bonded Leather Sofa',
            materials='Bonded Leather',
            image_url=''
        )
        result = self.classifier.classify_product(prod)
        self.assertEqual(result['image_status'], 'missing')
        self.assertIn('shopify_category_attributes', result['extracted_attributes'])

    def test_case_9_broken_image_does_not_break_extraction(self):
        """Test Case 9: Broken image does not break category attribute extraction."""
        prod = Product.objects.create(
            product_number='TEST-BROKEN-IMG',
            name='Empress Bonded Leather Sofa',
            materials='Bonded Leather',
            image_url='https://invalid-non-existent-domain-998877.org/broken.jpg'
        )
        result = self.classifier.classify_product(prod, check_image=True)
        self.assertEqual(result['image_status'], 'broken')
        self.assertIn('shopify_category_attributes', result['extracted_attributes'])

    def test_case_10_changing_category_updates_shopify_category_attributes(self):
        """Test Case 10: Changing the category updates the displayed Shopify Category Attributes."""
        prod = Product.objects.create(
            product_number='TEST-CAT-CHANGE',
            name='Convertible Piece',
            materials='Solid Wood'
        )
        ClassificationResult.objects.create(
            product=prod,
            predicted_category=self.cat_sofa_indoor,
            confidence_score=0.80,
            status='needs_review',
            extracted_attributes=extract_attributes(prod, self.cat_sofa_indoor)
        )

        # Update category to Dining Chairs
        resp = self.client.post(
            f'/api/v1/products/{prod.id}/update-category/',
            {'category_id': self.cat_chair.id, 'reviewer': 'Reviewer'},
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        cls_data = data['classification']
        self.assertEqual(cls_data['approved_category']['id'], self.cat_chair.id)

        # Attributes should now reflect Dining Chairs (no sectional shape)
        cat_attr_names = [a['attribute_name'] for a in cls_data['shopify_category_attributes']]
        self.assertNotIn('Sectional shape', cat_attr_names)

    def test_case_11_alternative_category_preview_and_selection(self):
        """Test Case 11: Choosing an alternative category updates Shopify Category Attributes."""
        prod = Product.objects.create(
            product_number='TEST-ALT-SELECT',
            name='Modern Bench Chair',
            materials='Wood',
            product_color='Brown'
        )
        ClassificationResult.objects.create(
            product=prod,
            predicted_category=self.cat_chair,
            confidence_score=0.75,
            status='auto_classified',
            extracted_attributes=extract_attributes(prod, self.cat_chair)
        )

        # Live category attributes preview API
        preview_resp = self.client.get(f'/api/v1/products/{prod.id}/category-attributes/?category_id={self.cat_sofa_indoor.id}')
        self.assertEqual(preview_resp.status_code, 200)
        preview_data = preview_resp.json()
        self.assertEqual(preview_data['category_id'], self.cat_sofa_indoor.id)
        self.assertIn('shopify_category_attributes', preview_data)

    def test_case_12_existing_batch_processing_and_export_functionality(self):
        """Test Case 12: Existing batch processing, fault isolation, approval, and export continue working."""
        p1 = Product.objects.create(product_number='BATCH-C1', name='Sectional Sofa', materials='Bonded Leather')
        p2 = Product.objects.create(product_number='BATCH-C2', name='Dining Chair', materials='Wood')
        ClassificationResult.objects.create(product=p1, status='pending')
        ClassificationResult.objects.create(product=p2, status='pending')

        job = start_batch_classification(chunk_size=10)
        import time
        for _ in range(30):
            job.refresh_from_db()
            if job.status == 'completed':
                break
            time.sleep(0.1)

        self.assertEqual(job.status, 'completed')
        self.assertEqual(job.processed_items, 2)

        # Export test
        csv_data, ctype, filename = export_classification_data(export_format='csv')
        self.assertIn('Shopify Category Attributes', csv_data)
        self.assertIn('Product Specifications', csv_data)
        self.assertIn('BATCH-C1', csv_data)

