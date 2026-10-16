# Creates genuine evidence of one order request/response. No real payments.
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
$evidenceDir = Join-Path $projectRoot "evidence"
New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

$correlationId = "assessment-" + [guid]::NewGuid().ToString()
$requestBody = @{
    customer_id = "demo-customer"
    sku = "HEADPHONES"
    quantity = 1
    payment_token = "mock-success"
} | ConvertTo-Json
$headers = @{
    "Idempotency-Key" = $correlationId
    "X-Correlation-ID" = $correlationId
}

Write-Host "REQUEST: POST http://localhost:8001/orders" -ForegroundColor Cyan
Write-Host "Idempotency-Key: $correlationId"
Write-Host "X-Correlation-ID: $correlationId"
Write-Host $requestBody

$response = Invoke-WebRequest -UseBasicParsing -Method Post `
    -Uri "http://localhost:8001/orders" -Headers $headers `
    -ContentType "application/json" -Body $requestBody -TimeoutSec 30
$order = $response.Content | ConvertFrom-Json
Write-Host "RESPONSE: HTTP $($response.StatusCode)" -ForegroundColor Green
$order | Select-Object order_id, status, amount_cents, currency, correlation_id, event_id, replayed |
    ConvertTo-Json | Write-Host

@{
    request = ($requestBody | ConvertFrom-Json)
    http_status = [int]$response.StatusCode
    correlation_id = $correlationId
    order = $order
    captured_at = (Get-Date).ToUniversalTime().ToString("o")
} | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 (Join-Path $evidenceDir "demonstration.json")
Write-Host "Order request/response evidence is ready. Keep this output visible."
