param(
    [int]$BatchCount = 10,
    [int]$QueriesPerBatch = 5
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = $PSScriptRoot
Set-Location $Root

$RunId = Get-Date -Format "yyyyMMdd_HHmmss"
$LogDir = Join-Path $Root "logs"
$OutputDir = Join-Path $Root "output"
$BatchDir = Join-Path $Root "batch_queries"

New-Item -ItemType Directory -Force -Path $LogDir, $OutputDir, $BatchDir | Out-Null

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)

$Completed = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
Get-ChildItem -Path $OutputDir -Filter "*.state.json" -File -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        $State = Get-Content -Path $_.FullName -Raw | ConvertFrom-Json
        foreach ($Query in @($State.completed_queries)) {
            if ($null -ne $Query -and [string]$Query -ne "") {
                [void]$Completed.Add([string]$Query)
            }
        }
    }
    catch {
        Write-Warning "Could not read state file $($_.FullName): $($_.Exception.Message)"
    }
}

$AreaQueryPattern = "sector \d+ noida|phase \d+ noida|industrial area|udyog vihar|manesar|sahibabad|kavi nagar|nit faridabad|bulandshahr road"
$AllQueries = Get-Content -Path (Join-Path $Root "queries.txt") |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -and -not $_.StartsWith("#") }

$TargetCount = $BatchCount * $QueriesPerBatch
$Selected = [System.Collections.Generic.List[string]]::new()
foreach ($Query in $AllQueries) {
    if ($Query -match $AreaQueryPattern -and -not $Completed.Contains($Query)) {
        $Selected.Add($Query)
        if ($Selected.Count -ge $TargetCount) {
            break
        }
    }
}

if ($Selected.Count -eq 0) {
    throw "No remaining area-wise exporter queries found in queries.txt."
}

$SummaryLog = Join-Path $LogDir "area_exporters_$RunId.summary.log"
"Run started: $(Get-Date -Format o)" | Set-Content -Path $SummaryLog -Encoding UTF8
"Root: $Root" | Add-Content -Path $SummaryLog -Encoding UTF8
"Python: $Python" | Add-Content -Path $SummaryLog -Encoding UTF8
"Requested batches: $BatchCount; queries per batch: $QueriesPerBatch; selected queries: $($Selected.Count)" | Add-Content -Path $SummaryLog -Encoding UTF8

for ($BatchNumber = 1; $BatchNumber -le $BatchCount; $BatchNumber++) {
    $StartIndex = ($BatchNumber - 1) * $QueriesPerBatch
    if ($StartIndex -ge $Selected.Count) {
        break
    }

    $BatchQueries = $Selected | Select-Object -Skip $StartIndex -First $QueriesPerBatch
    $BatchFile = Join-Path $BatchDir ("area_exporters_{0}_batch_{1:D2}.txt" -f $RunId, $BatchNumber)
    $StdOutLog = Join-Path $LogDir ("area_exporters_{0}_batch_{1:D2}.out.log" -f $RunId, $BatchNumber)
    $StdErrLog = Join-Path $LogDir ("area_exporters_{0}_batch_{1:D2}.err.log" -f $RunId, $BatchNumber)
    $OutputFile = Join-Path $OutputDir ("area_exporter_market_leads_batch_{0:D2}.csv" -f $BatchNumber)

    [System.IO.File]::WriteAllLines($BatchFile, [string[]]$BatchQueries, $Utf8NoBom)
    "" | Add-Content -Path $SummaryLog -Encoding UTF8
    "Batch $BatchNumber started: $(Get-Date -Format o)" | Add-Content -Path $SummaryLog -Encoding UTF8
    "Query file: $BatchFile" | Add-Content -Path $SummaryLog -Encoding UTF8
    $BatchQueries | ForEach-Object { "  - $_" | Add-Content -Path $SummaryLog -Encoding UTF8 }

    & $Python (Join-Path $Root "scrape_maps.py") --query-file $BatchFile --max-results 0 --batch-size 25 --output $OutputFile 1> $StdOutLog 2> $StdErrLog
    $ExitCode = $LASTEXITCODE

    "Batch $BatchNumber finished: $(Get-Date -Format o); exit code: $ExitCode" | Add-Content -Path $SummaryLog -Encoding UTF8
    "Stdout: $StdOutLog" | Add-Content -Path $SummaryLog -Encoding UTF8
    "Stderr: $StdErrLog" | Add-Content -Path $SummaryLog -Encoding UTF8

    if ($ExitCode -ne 0) {
        "Stopping after batch $BatchNumber because the scraper returned exit code $ExitCode." | Add-Content -Path $SummaryLog -Encoding UTF8
        exit $ExitCode
    }
}

"Run finished: $(Get-Date -Format o)" | Add-Content -Path $SummaryLog -Encoding UTF8
