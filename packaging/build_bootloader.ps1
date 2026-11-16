# Build PyInstaller with a bootloader compiled from source on THIS machine, install it,
# and prove it: the compiled run.exe / runw.exe must differ from the prebuilt ones in the
# official wheel (the prebuilt bootloader is shared by every PyInstaller app, so AV
# heuristics key on it).
#
#   powershell -ExecutionPolicy Bypass -File packaging\build_bootloader.ps1 [-Python python] [-Version 6.22.3]
#
# Needs MSVC ("Build Tools for Visual Studio" -> "Desktop development with C++").
param(
  [string]$Python = "python",
  [string]$Version = "6.22.3"
)
$ErrorActionPreference = 'Stop'
if (Test-Path $Python) { $Python = (Resolve-Path $Python).Path }   # survives Push-Location
$work = Join-Path ([IO.Path]::GetTempPath()) "pyi-bootloader-$Version"
if (Test-Path $work) { Remove-Item -Recurse -Force $work }
New-Item -ItemType Directory $work | Out-Null

function Invoke-Native([string]$what) {
  if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" }
}

Write-Host "== downloading PyInstaller $Version source"
& $Python -m pip download --no-binary=:all: --no-deps "pyinstaller==$Version" -d $work
Invoke-Native "pip download sdist"
$sdist = Get-ChildItem $work -Filter "pyinstaller-$Version.tar.gz" | Select-Object -First 1
tar -xzf $sdist.FullName -C $work
Invoke-Native "tar"
$src = Join-Path $work "pyinstaller-$Version"

Write-Host "== compiling bootloader (waf)"
Push-Location (Join-Path $src "bootloader")
try {
  & $Python ./waf distclean all
  Invoke-Native "waf bootloader build"
} finally { Pop-Location }

Write-Host "== installing PyInstaller from the compiled source tree"
& $Python -m pip uninstall -y pyinstaller 2>$null | Out-Null
& $Python -m pip install --no-cache-dir $src
Invoke-Native "pip install PyInstaller"

Write-Host "== verifying the installed bootloader is NOT the prebuilt one"
$whl = Join-Path $work "wheel"
& $Python -m pip download --only-binary=:all: --no-deps --platform win_amd64 "pyinstaller==$Version" -d $whl
Invoke-Native "pip download official wheel"
$pyiDir = (& $Python -c "import PyInstaller, os; print(os.path.dirname(PyInstaller.__file__))").Trim()
$official = Join-Path $work "official"
& $Python -c "import zipfile,sys,glob; zipfile.ZipFile(glob.glob(sys.argv[1]+'/*.whl')[0]).extractall(sys.argv[2])" $whl $official
Invoke-Native "extract wheel"
foreach ($exe in 'run.exe', 'runw.exe') {
  $mine = Get-FileHash -Algorithm SHA256 (Join-Path $pyiDir "bootloader\Windows-64bit-intel\$exe")
  $theirs = Get-FileHash -Algorithm SHA256 (Join-Path $official "PyInstaller\bootloader\Windows-64bit-intel\$exe")
  Write-Host ("  {0,-9} compiled {1}`n            prebuilt {2}" -f $exe, $mine.Hash.ToLower(), $theirs.Hash.ToLower())
  if ($mine.Hash -eq $theirs.Hash) { throw "$exe is identical to the prebuilt bootloader - source build did not take effect" }
}
Write-Host "OK: PyInstaller $Version with locally compiled bootloader"
exit 0
