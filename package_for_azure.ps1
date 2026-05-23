# Builds deploy.zip in the project root, containing only the files Azure App
# Service actually needs to run main1.py. Keep the exclusion list in sync
# with .deployignore (which exists only as documentation).
#
# Usage:
#   .\package_for_azure.ps1
#
# Produces: deploy.zip  (upload this in step 4 of AZURE_DEPLOY.md)

$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$zipPath     = Join-Path $projectRoot "deploy.zip"
$stagingDir  = Join-Path $projectRoot ".azure_staging"

# Paths excluded from the deployment zip. Anything not in this list AND
# matching the runtime needs (see AZURE_DEPLOY.md) will be packaged.
$excludeDirs = @(
    ".venv",
    "ai",
    "__pycache__",
    ".git",
    ".vscode",
    ".idea",
    "New folder",
    "static",
    "templates",
    ".azure_staging"
)
$excludeFiles = @(
    "Proposal.pdf",
    "Proposal1.pdf",
    "analytics.db",
    "manifest.xml",
    "main.py",
    "server.py",
    "login.py",
    "apollo_crm.log",
    "linkedin_dump.html",
    "apollo_contacts.json",
    "marketing_contacts.json",
    "marketing_campaigns.json",
    "processed_linkedin_message_ids.json",
    ".env",
    "deploy.zip",
    "package_for_azure.ps1",
    ".deployignore",
    ".gitignore",
    "AZURE_DEPLOY.md"
)
$excludeExtensions = @(".pyc", ".pyo", ".pyd")

# Clean previous run
if (Test-Path $stagingDir) { Remove-Item $stagingDir -Recurse -Force }
if (Test-Path $zipPath)    { Remove-Item $zipPath    -Force }

New-Item -ItemType Directory -Path $stagingDir | Out-Null

Write-Host "Staging files into $stagingDir ..." -ForegroundColor Cyan

# Copy every item from project root into staging, applying exclusions.
Get-ChildItem -Path $projectRoot -Force | ForEach-Object {
    $item = $_

    # Skip excluded directories
    if ($item.PSIsContainer -and $excludeDirs -contains $item.Name) {
        Write-Host "  skip dir : $($item.Name)" -ForegroundColor DarkGray
        return
    }

    # Skip excluded files
    if (-not $item.PSIsContainer) {
        if ($excludeFiles -contains $item.Name) {
            Write-Host "  skip file: $($item.Name)" -ForegroundColor DarkGray
            return
        }
        $ext = [System.IO.Path]::GetExtension($item.Name)
        if ($excludeExtensions -contains $ext) {
            Write-Host "  skip file: $($item.Name)" -ForegroundColor DarkGray
            return
        }
    }

    $destination = Join-Path $stagingDir $item.Name
    if ($item.PSIsContainer) {
        Copy-Item -Path $item.FullName -Destination $destination -Recurse -Force
    } else {
        Copy-Item -Path $item.FullName -Destination $destination -Force
    }
    Write-Host "  include : $($item.Name)" -ForegroundColor Green
}

# Compress staging into deploy.zip at project root
Write-Host "Creating $zipPath ..." -ForegroundColor Cyan
Compress-Archive -Path (Join-Path $stagingDir '*') -DestinationPath $zipPath -Force

# Clean up staging
Remove-Item $stagingDir -Recurse -Force

$zipSizeMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 2)
Write-Host ""
Write-Host "Done. deploy.zip ($zipSizeMB MB) ready at:" -ForegroundColor Green
Write-Host "  $zipPath"
Write-Host ""
Write-Host "Next: follow AZURE_DEPLOY.md from step 4 (az webapp deploy)." -ForegroundColor Yellow
