param([string]$EnvFile = ".env")

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$destination = [IO.Path]::GetFullPath((Join-Path $projectRoot $EnvFile))
if (Test-Path -LiteralPath $destination) {
    throw "$destination already exists. Keep its credentials; edit it manually if needed."
}

function New-RandomSecret {
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

$content = [IO.File]::ReadAllText((Join-Path $projectRoot ".env.example"))
foreach ($key in @("POSTGRES_PASSWORD", "MOCK_WEBHOOK_TOKEN", "OPERATOR_API_TOKEN")) {
    $content = [regex]::Replace($content, "(?m)^$key=.*$", "$key=$(New-RandomSecret)")
}
$content = $content.Replace("APP_ENV_FILE=.env", "APP_ENV_FILE=$EnvFile")
[IO.File]::WriteAllText($destination, $content, (New-Object Text.UTF8Encoding($false)))
Write-Host "Created $EnvFile with three independent random secrets. No Python installation needed."
Write-Host "Run: docker compose --env-file $EnvFile up -d --build"
