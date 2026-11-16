# Zip dist\N1MM_timer and write dist\SHA256SUMS.txt (exe files + zip).
param([Parameter(Mandatory = $true)][string]$Version)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$zip = "dist\N1MM_timer-$Version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path 'dist\N1MM_timer\*' -DestinationPath $zip
$files = @(Get-ChildItem 'dist\N1MM_timer\*.exe') + @(Get-Item $zip)
$files | Get-FileHash -Algorithm SHA256 |
  ForEach-Object { '{0}  {1}' -f $_.Hash.ToLower(), (Split-Path $_.Path -Leaf) } |
  Set-Content -Encoding ascii 'dist\SHA256SUMS.txt'
Write-Host "wrote $zip and dist\SHA256SUMS.txt"
