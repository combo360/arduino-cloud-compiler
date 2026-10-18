<#
.SYNOPSIS
  Checks a deployed i-Droid compile server end to end.

.DESCRIPTION
  Run this after the Render service shows "Live" (or against a local Docker
  container). It checks health, that a missing key is rejected, that a real
  sketch compiles to a .hex, and the read-only endpoints the app uses.
  Exits 0 when every check passes, 1 otherwise.

.EXAMPLE
  .\verify-deploy.ps1 -BaseUrl https://idroid-compile-server.onrender.com -ApiKey KEY1

.EXAMPLE
  .\verify-deploy.ps1 -BaseUrl http://localhost:8080 -ApiKey KEY1 -AdminKey ADMINKEY
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BaseUrl,

    [string]$ApiKey = '',

    [string]$AdminKey = '',

    [string]$Fqbn = 'arduino:avr:uno',

    [int]$TimeoutSec = 180
)

$ErrorActionPreference = 'Stop'
$BaseUrl = $BaseUrl.TrimEnd('/')

# Windows PowerShell 5.1 throws on 4xx/5xx responses, so requests are made
# through a helper that reports the status code instead of throwing.
function Invoke-Api {
    param(
        [string]$Method = 'GET',
        [string]$Path,
        $Payload = $null,
        [string]$Key = ''
    )

    $uri = $BaseUrl + $Path
    $headers = @{}
    if ($Key) { $headers['X-API-Key'] = $Key }

    $params = @{
        Uri                = $uri
        Method             = $Method
        Headers            = $headers
        TimeoutSec         = $TimeoutSec
        UseBasicParsing    = $true
    }
    if ($null -ne $Payload) {
        $json = ConvertTo-Json -InputObject $Payload -Depth 8 -Compress
        $params['Body'] = [System.Text.Encoding]::UTF8.GetBytes($json)
        $params['ContentType'] = 'application/json; charset=utf-8'
    }

    try {
        $response = Invoke-WebRequest @params
        return [pscustomobject]@{
            Status = [int]$response.StatusCode
            Text   = $response.Content
            Failed = $false
        }
    }
    catch {
        $webResponse = $_.Exception.Response
        if ($null -eq $webResponse) {
            return [pscustomobject]@{
                Status = 0
                Text   = $_.Exception.Message
                Failed = $true
            }
        }
        $status = 0
        try { $status = [int]$webResponse.StatusCode } catch { $status = 0 }
        $body = ''
        try {
            $reader = New-Object System.IO.StreamReader($webResponse.GetResponseStream())
            $body = $reader.ReadToEnd()
            $reader.Dispose()
        }
        catch { $body = $_.Exception.Message }
        return [pscustomobject]@{
            Status = $status
            Text   = $body
            Failed = $true
        }
    }
}

$results = New-Object System.Collections.ArrayList

function Add-Result {
    param([string]$Check, [bool]$Pass, [string]$Detail)
    [void]$results.Add([pscustomobject]@{
        Check  = $Check
        Result = $(if ($Pass) { 'PASS' } else { 'FAIL' })
        Detail = $Detail
    })
    $colour = 'Green'
    if (-not $Pass) { $colour = 'Red' }
    Write-Host ("[{0}] {1}: {2}" -f $(if ($Pass) { 'PASS' } else { 'FAIL' }), $Check, $Detail) -ForegroundColor $colour
}

Write-Host "i-Droid server: $BaseUrl" -ForegroundColor Cyan
if ($ApiKey) { Write-Host ("API key: {0}...{1}" -f $ApiKey.Substring(0, [Math]::Min(6, $ApiKey.Length)), $ApiKey.Substring([Math]::Max(0, $ApiKey.Length - 4))) }
Write-Host ''

# 1. Health (the Render health check uses this path).
$health = Invoke-Api -Path '/health'
$healthOk = $false
$healthText = $health.Text
if ($health.Status -eq 200) {
    try {
        $healthJson = $healthText | ConvertFrom-Json
        $healthOk = ($healthJson.status -eq 'ok')
    }
    catch { $healthOk = $false }
}
if ($health.Status -eq 0) {
    Add-Result 'Health' $false ("No answer from the server: " + $healthText + " (is the URL right, and is the service awake?)")
}
else {
    Add-Result 'Health' $healthOk ("HTTP " + $health.Status + ' ' + $healthText)
}

# 2. A request without a key must be refused before anything else is tested.
$noKey = Invoke-Api -Method 'POST' -Path '/v1/compile' -Payload @{
    sketch = 'void setup(){}
void loop(){}'
    fqbn   = $Fqbn
}
Add-Result 'Rejects a request without a key' ($noKey.Status -eq 401) ("HTTP " + $noKey.Status)

# 3. Real compile of a sketch that needs a shared library (Servo).
$sketch = @'
#include <Servo.h>
Servo s;
void setup() { s.attach(9); }
void loop() { s.write(90); delay(1000); }
'@

$compile = $null
if ($ApiKey) {
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $compile = Invoke-Api -Method 'POST' -Path '/v1/compile' -Key $ApiKey -Payload @{
        sketch = $sketch
        fqbn   = $Fqbn
    }
    $stopwatch.Stop()
    $elapsed = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 1)

    $compileOk = $false
    $detail = 'HTTP ' + $compile.Status + ' - ' + $compile.Text
    if ($compile.Status -eq 200) {
        try {
            $parsed = $compile.Text | ConvertFrom-Json
            $hasOutput = (-not [string]::IsNullOrEmpty($parsed.hex)) -or (-not [string]::IsNullOrEmpty($parsed.binary))
            $compileOk = ([bool]$parsed.success) -and $hasOutput
            $detail = ("success=" + $parsed.success + ", " + $parsed.format + "=" + $(if ($parsed.hex) { $parsed.hex.Length } else { 0 }) + ' chars, ' + $elapsed + ' s')
        }
        catch { $detail = 'Response was not JSON: ' + $compile.Text }
    }
    Add-Result 'Compiles a sketch' $compileOk $detail
}
else {
    Write-Host '[SKIP] Compiles a sketch: no -ApiKey given' -ForegroundColor Yellow
}

# 4. Read-only endpoints the app calls on start-up.
if ($ApiKey) {
    $boards = Invoke-Api -Path '/v1/boards/installed' -Key $ApiKey
    $boardCount = 0
    $hasUno = $false
    if ($boards.Status -eq 200) {
        try {
            $list = @($boards.Text | ConvertFrom-Json)
            $boardCount = $list.Count
            foreach ($b in $list) { if ($b.fqbn -eq $Fqbn) { $hasUno = $true } }
        }
        catch { }
    }
    Add-Result 'Lists installed boards' (($boards.Status -eq 200) -and $hasUno) ("HTTP " + $boards.Status + ", " + $boardCount + ' board(s), contains ' + $Fqbn)

    $examples = Invoke-Api -Path ('/v1/examples?fqbn=' + [uri]::EscapeDataString($Fqbn)) -Key $ApiKey
    $groupCount = 0
    if ($examples.Status -eq 200) {
        try { $groupCount = @(($examples.Text | ConvertFrom-Json).groups).Count } catch { }
    }
    Add-Result 'Lists examples' (($examples.Status -eq 200) -and ($groupCount -gt 0)) ("HTTP " + $examples.Status + ", " + $groupCount + ' group(s)')

    $format = Invoke-Api -Method 'POST' -Path '/v1/format' -Key $ApiKey -Payload @{ code = 'void loop(){for(int i=0;i<3;i++){}}' }
    $formatOk = $false
    $formatLength = 0
    if ($format.Status -eq 200) {
        try {
            $formatted = ($format.Text | ConvertFrom-Json).formatted
            $formatLength = ([string]$formatted).Length
            $formatOk = $formatLength -gt 0
        }
        catch { }
    }
    Add-Result 'Auto Format works' $formatOk ("HTTP " + $format.Status + ", " + $formatLength + ' chars')

    $libs = Invoke-Api -Path '/v1/libraries/installed' -Key $ApiKey
    $libCount = 0
    if ($libs.Status -eq 200) {
        try { $libCount = @($libs.Text | ConvertFrom-Json).Count } catch { }
    }
    Add-Result "Lists the caller's libraries" ($libs.Status -eq 200) ("HTTP " + $libs.Status + ", " + $libCount + ' library(ies)')
}

# 5. Admin key, if supplied.
if ($AdminKey) {
    $urls = Invoke-Api -Path '/v1/boards/urls' -Key $AdminKey
    Add-Result 'Admin key accepted' ($urls.Status -eq 200) ("HTTP " + $urls.Status)

    $boardsWithAdmin = Invoke-Api -Path '/v1/boards/installed' -Key $AdminKey
    Add-Result 'Admin key compiles/lists' ($boardsWithAdmin.Status -eq 200) ("HTTP " + $boardsWithAdmin.Status)
}

Write-Host ''
$failed = @($results | Where-Object { $_.Result -eq 'FAIL' })
$results | Format-Table -AutoSize

if ($failed.Count -eq 0) {
    Write-Host ("All {0} checks passed." -f $results.Count) -ForegroundColor Green
    exit 0
}
Write-Host ("{0} of {1} checks failed." -f $failed.Count, $results.Count) -ForegroundColor Red
exit 1
