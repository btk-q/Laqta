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
$existing = gh release list --limit 100 --json tagName --jq ".[].tagName"
if ($existing -contains $tag) { throw "Release $tag already exists. Raise the number in laqta\__init__.py." }

$file = Join-Path $env:TEMP "laqta-notes-$version.md"
# Fails here, before the slow build, if the patch notes have not been written.
& $py packaging
otes.py $version "no-zip-yet" $file
if ($LASTEXITCODE -ne 0) { throw "Write the patch notes first: add a '## $version' section to CHANGELOG.md." }

& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed; nothing was published." }

powershell -NoProfile -File packaging\build.ps1
if ($LASTEXITCODE -ne 0) { throw "Build failed; nothing was published." }
$zip = "dist\Laqta-$version-win64.zip"

& $py packaging
otes.py $version $zip $file   # again, now with the zip's hash

git push origin HEAD
gh release create $tag $zip --title "Laqta $version" --notes-file $file
if ($LASTEXITCODE -ne 0) { throw "gh could not create the release." }
Remove-Item $file
"Published $tag. Running copies of Laqta will offer it at their next start."
