param([string]$OutputDirectory = ".local/docker-handover")

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$destination = [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
if (Test-Path -LiteralPath $destination) {
    throw "$destination already exists. Choose a new output directory."
}

Push-Location $projectRoot
try {
    $configText = docker compose config --format json
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose configuration is invalid." }
    $configuration = ($configText -join "`n") | ConvertFrom-Json
    $appImage = $configuration.services.api.image
    $dbImage = $configuration.services.db.image
    foreach ($imageName in @($appImage, $dbImage)) {
        $imageId = docker image inspect --format '{{.Id}}' $imageName
        if ($LASTEXITCODE -ne 0) { throw "Missing $imageName. Build/pull the image first." }
    }
    New-Item -ItemType Directory -Path (Join-Path $destination "scripts") | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $destination "data/raw") | Out-Null
    $archive = Join-Path $destination "images.tar"
    docker image save --output $archive $appImage $dbImage
    if ($LASTEXITCODE -ne 0) { throw "Docker image export failed." }
    Copy-Item -LiteralPath (Join-Path $projectRoot "compose.yaml") -Destination $destination
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "setup-docker.ps1") -Destination (Join-Path $destination "scripts")
    $template = [IO.File]::ReadAllText((Join-Path $projectRoot ".env.example"))
    $template = [regex]::Replace($template, '(?m)^APP_IMAGE=.*$', "APP_IMAGE=$appImage")
    [IO.File]::WriteAllText((Join-Path $destination ".env.example"), $template, (New-Object Text.UTF8Encoding($false)))
    $instructions = @'
Prerequisite: Docker with Compose v2 and Linux containers, on the same CPU architecture as the images.
Open PowerShell in this folder and run:

  docker image load -i images.tar
  .\scripts\setup-docker.ps1
  docker compose up -d --no-build --pull never --wait --wait-timeout 180

Open http://127.0.0.1:8000/demo/admin and use the two API tokens from your newly created .env.
Verify processing: docker compose exec api python -m scripts.smoke_docker
Stop without deleting records: docker compose down

The image contains E5 and simulated demo materials; default fake mode needs no external LLM.
For Gemini, edit LLM_PROVIDER, GEMINI_API_KEY and GEMINI_MODEL in .env and rerun compose up -d.
This package contains no original raw materials, live database, issued staff credentials or secrets.
Issue class-scoped staff credentials with scripts.seed_teacher_demo through docker compose exec api.
Back up postgres_data, generated_data and staff_credentials before moving an existing instance.
Do not use docker compose down -v if you need the stored records.
'@
    [IO.File]::WriteAllText((Join-Path $destination "START-HERE.txt"), $instructions)
    $checksum = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText((Join-Path $destination "images.tar.sha256"), "$checksum  images.tar`n")
    Write-Host "Handover package created at $destination (images, Compose, env template and setup; no secrets)."
} finally {
    Pop-Location
}
