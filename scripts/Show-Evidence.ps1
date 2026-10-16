# Shows actual Order and Notification logs for the order made by Demo.ps1.
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
$evidenceDir = Join-Path $projectRoot "evidence"
$demo = Get-Content (Join-Path $evidenceDir "demonstration.json") -Raw | ConvertFrom-Json
$correlationId = $demo.correlation_id
$found = @()

for ($attempt = 0; $attempt -lt 30; $attempt++) {
    $raw = @(docker compose logs --no-color --tail 1000 order notification 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "Could not read Docker logs." }
    $found = @($raw | ForEach-Object {
        $line = $_.ToString()
        $jsonStart = $line.IndexOf('{')
        if ($jsonStart -ge 0) {
            try {
                $record = $line.Substring($jsonStart) | ConvertFrom-Json
                if ($record.correlation_id -eq $correlationId -and
                    $record.event -in @("order_confirmed", "event_consumed")) {
                    $record
                }
            } catch { }
        }
    })
    if (($found | Where-Object event -eq "order_confirmed") -and
        ($found | Where-Object event -eq "event_consumed")) { break }
    Start-Sleep -Seconds 1
}

$raw | Set-Content -Encoding UTF8 (Join-Path $evidenceDir "docker-logs.txt")
if (!(($found | Where-Object event -eq "order_confirmed") -and
      ($found | Where-Object event -eq "event_consumed"))) {
    throw "Both required log events have not appeared. Send this error text."
}

Write-Host "ACTUAL SERVICE LOGS - SAME CORRELATION ID" -ForegroundColor Cyan
$found | Sort-Object timestamp | ForEach-Object {
    $_ | Select-Object timestamp, service, event, correlation_id, order_id, event_id |
        ConvertTo-Json | Write-Host
}
