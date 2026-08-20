"""
WSGI config for shopify_classifier project.
"""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shopify_classifier.settings')
application = get_wsgi_application()
