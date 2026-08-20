"""
ASGI config for shopify_classifier project.
"""
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shopify_classifier.settings')
application = get_asgi_application()
