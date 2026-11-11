# Publishes a new version: tests, build, then a GitHub release carrying the zip and the
# patch notes. Everyone's Laqta sees the release the next time it starts and offers the update.
#
#   1. Set the new number in laqta\__init__.py            (for example 0.3.1)
#   2. Add a "## 0.3.1" section at the top of CHANGELOG.md  (these are the patch notes people read)
#   3. Commit, then run:  powershell -File packaging\release.ps1
$ErrorActionPreference = "Stop"
$py = ".\.venv\Scripts\python.exe"

$version = & $py -c "import laqta; print(laqta.__version__)"
$tag = "v$version"

if (git status --porcelain) { throw "Commit your changes first: the release must match what is in git." }
gh release view $tag *> $null
if ($LASTEXITCODE -eq 0) { throw "Release $tag already exists. Raise the number in laqta\__init__.py." }

# The patch notes are this version's section of CHANGELOG.md.
$notes = & $py -c "import re,sys,pathlib; t=pathlib.Path('CHANGELOG.md').read_text(encoding='utf-8'); m=re.search(r'(?ms)^## +v?' + re.escape(sys.argv[1]) + r'\b[^\n]*\n(.*?)(?=^## |\Z)', t); print(m.group(1).strip() if m else '')" $version
if (-not $notes) { throw "CHANGELOG.md has no '## $version' section. Write the patch notes first." }

& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed; nothing was published." }

powershell -NoProfile -File packaging\build.ps1
if ($LASTEXITCODE -ne 0) { throw "Build failed; nothing was published." }
$zip = "dist\Laqta-$version-win64.zip"

# The updater refuses a download whose hash it cannot check. GitHub lists one itself;
# this line in the notes is the fallback.
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
$file = Join-Path $env:TEMP "laqta-notes-$version.md"
[IO.File]::WriteAllText($file, ($notes -join "`n") + "`n`nSHA256: $hash`n")

git push origin HEAD
gh release create $tag $zip --title "Laqta $version" --notes-file $file
if ($LASTEXITCODE -ne 0) { throw "gh could not create the release." }
Remove-Item $file
"Published $tag. Running copies of Laqta will offer it at their next start."
