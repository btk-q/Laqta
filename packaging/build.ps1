# Builds dist\Laqta\Laqta.exe and dist\Laqta-<version>-win64.zip.
# Run from the repository root:  powershell -File packaging\build.ps1
$ErrorActionPreference = "Stop"
$py = ".\.venv\Scripts\python.exe"

& $py -m pip install -q --disable-pip-version-check pyinstaller
& $py packaging\make_icon.py

# PyInstaller's scratch files go outside the project: its work folder contains a Laqta.exe that
# cannot run, and it was mistaken for the real one (which is dist\Laqta\Laqta.exe).
$work = Join-Path $env:TEMP "laqta-build"

# One folder, not one file: starts faster and trips fewer antivirus heuristics.
& $py -m PyInstaller --noconfirm --clean --windowed --name Laqta `
    --icon (Resolve-Path packaging\laqta.ico) `
    --collect-data laqta `
    --exclude-module tkinter --exclude-module pytest `
    --distpath dist --workpath $work --specpath $work `
    packaging\run_laqta.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$version = & $py -c "import laqta; print(laqta.__version__)"
$zip = "dist\Laqta-$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path dist\Laqta -DestinationPath $zip
"Built $zip ({0:N0} MB)" -f ((Get-Item $zip).Length / 1MB)
