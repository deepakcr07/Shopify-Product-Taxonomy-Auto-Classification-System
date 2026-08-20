Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "Starting Shopify Product Taxonomy Classifier Web Server" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

& "d:\Python_test_app\venv\Scripts\python.exe" manage.py runserver 127.0.0.1:8000
