<#
.SYNOPSIS
Export the PII-free, reusable parts of this workspace into a standalone template dir.

.DESCRIPTION
This repo doubles as a personal job-search workspace AND the seed for an open-source
"job-seeking-template" that anyone can fork. That means most of the tree is PII: tailored
CVs, per-company research, HR/legal notes, salary figures, real names. This script copies
ONLY the reusable, PII-free scaffolding (deterministic core, skills, docs, config templates)
into a target directory, and NEVER copies candidate data.

Included:
  src/                       deterministic core (jobcore) + shared scripts
  .claude/skills/            active skills, EXCLUDING _legacy/
  .claude/commands/          slash-command definitions
  .claude/README.md          skills/commands readme
  .claude/settings.local.json.example   (the *.example.* config template only)
  scripts/                   this + the sync scripts (see -ExcludeSelf)
  docs/                      framework docs
  _template/                 root template scaffolding, if present
  positions/_template/       the ONE allowed positions subtree (per-position scaffold)
  .github/                   CI + workflows
  pyproject.toml, .python-version, README.md, .gitignore, .gitattributes
  claude-skills.lock
  *.example.* files          e.g. .env.example, ats_targets.example.json

NEVER copied (candidate data / secrets / local state):
  positions/            (except positions/_template/)   job-tracking/
  .env   ats_targets.json   .memory-backup/   .claude-global-backup/
  .claude/settings.local.json   (the non-example, real local settings)

.IMPORTANT  --  PII lives in git history, not just the working tree.
This script copies working-tree FILES only. It does NOT copy .git/, and you should NOT
`git clone` this repo to make the template: the commit history is full of real CVs, names,
salaries and HR/legal material. Start the template with a FRESH history instead:

    ./scripts/export-template.ps1 -TargetDir ../job-seeking-template
    cd ../job-seeking-template
    git init && git add -A && git commit -m "Initial import: job-seeking template"

.PARAMETER TargetDir
Destination directory. Default: ../job-seeking-template (relative to the repo root).

.PARAMETER DryRun
List every path that WOULD be copied, without copying anything.

.PARAMETER Force
Allow copying into a TargetDir that already exists and is non-empty (files are overwritten).

.PARAMETER ExcludeSelf
Do not copy this script (export-template.ps1) into the template's scripts/ dir.

.EXAMPLE
.\scripts\export-template.ps1 -DryRun
.\scripts\export-template.ps1
.\scripts\export-template.ps1 -TargetDir C:\code\job-seeking-template -Force
#>
[CmdletBinding()]
param(
    [string]$TargetDir = "../job-seeking-template",
    [switch]$DryRun,
    [switch]$Force,
    [switch]$ExcludeSelf
)

$ErrorActionPreference = "Stop"

# Repo root = parent of this script's dir (scripts/).
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# Resolve TargetDir relative to the repo root when a relative path is given.
if ([System.IO.Path]::IsPathRooted($TargetDir)) {
    $target = $TargetDir
} else {
    $target = Join-Path $repoRoot $TargetDir
}
# Normalize (the target need not exist yet).
$target = [System.IO.Path]::GetFullPath($target)

if ($target -eq $repoRoot) {
    Write-Error "TargetDir resolves to the repo root itself — refusing to export onto the source."
    exit 1
}

# Guardrails: paths that must NEVER be copied even if some future glob would match them.
$denyList = @(
    "positions",           # candidate work (except positions/_template, handled explicitly)
    "job-tracking",        # master CV, rankings, hearing/legal notes
    ".env",
    "ats_targets.json",
    ".memory-backup",
    ".claude-global-backup",
    ".claude/settings.local.json"
) | ForEach-Object { ($_ -replace '\\', '/').TrimEnd('/') }

# Explicit allow-exceptions that override the deny list (e.g. positions/_template is
# the one positions/ subtree we DO export).
$allowExceptions = @("positions/_template") | ForEach-Object { ($_ -replace '\\', '/').TrimEnd('/') }

function Test-Denied {
    param([string]$RelPath)
    $rel = ($RelPath -replace '\\', '/').TrimEnd('/')
    foreach ($a in $allowExceptions) {
        if ($rel -eq $a -or $rel.StartsWith($a + "/")) { return $false }
    }
    foreach ($d in $denyList) {
        if ($rel -eq $d -or $rel.StartsWith($d + "/")) { return $true }
    }
    return $false
}

# Build junk that should never land in the template regardless of source dir.
function Test-Junk {
    param([string]$RelPath)
    $rel = ($RelPath -replace '\\', '/')
    if ($rel -match '(^|/)__pycache__/' ) { return $true }
    if ($rel -match '(^|/)\.pytest_cache/' ) { return $true }
    if ($rel -match '(^|/)\.ruff_cache/' ) { return $true }
    if ($rel.EndsWith(".pyc")) { return $true }
    if ($rel -match '(^|/)\.DS_Store$') { return $true }
    return $false
}

$copyCount = 0
$skipCount = 0

# Copy a single file, preserving its path relative to the repo root.
function Copy-TemplateFile {
    param([string]$RelPath)
    $rel = ($RelPath -replace '\\', '/')
    $srcFull = Join-Path $repoRoot $rel
    if (-not (Test-Path $srcFull -PathType Leaf)) { return }
    if (Test-Junk $rel) { return }
    if (Test-Denied $rel) {
        Write-Host "[DENY] $rel" -ForegroundColor DarkYellow
        $script:skipCount++
        return
    }
    $dstFull = Join-Path $target $rel
    if ($DryRun) {
        Write-Host "[DRY] file  $rel" -ForegroundColor Cyan
    } else {
        $dstParent = Split-Path $dstFull -Parent
        if (-not (Test-Path $dstParent)) { New-Item -ItemType Directory -Path $dstParent -Force | Out-Null }
        Copy-Item -LiteralPath $srcFull -Destination $dstFull -Force
        Write-Host "[COPY] file  $rel" -ForegroundColor Green
    }
    $script:copyCount++
}

# Copy a whole directory tree, honoring the deny list and optional relative sub-path excludes.
function Copy-TemplateDir {
    param(
        [string]$RelDir,
        [string[]]$ExcludeRel = @()   # repo-relative paths (dirs) to skip
    )
    $relDirNorm = ($RelDir -replace '\\', '/').TrimEnd('/')
    $srcFull = Join-Path $repoRoot $relDirNorm
    if (-not (Test-Path $srcFull -PathType Container)) { return }
    $excludeNorm = $ExcludeRel | ForEach-Object { ($_ -replace '\\', '/').TrimEnd('/') }
    $rootLen = $repoRoot.Length + 1
    Get-ChildItem -Path $srcFull -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($rootLen) -replace '\\', '/'
        $excluded = $false
        foreach ($ex in $excludeNorm) {
            if ($rel -eq $ex -or $rel.StartsWith($ex + "/")) { $excluded = $true; break }
        }
        if ($excluded) { return }
        Copy-TemplateFile $rel
    }
}

Write-Host "Export template" -ForegroundColor White
Write-Host "  source: $repoRoot"
Write-Host "  target: $target"
if ($DryRun) { Write-Host "  mode:   DRY RUN (nothing written)" -ForegroundColor Cyan }
Write-Host ""

if (-not $DryRun) {
    if ((Test-Path $target) -and (Get-ChildItem -Path $target -Force | Select-Object -First 1)) {
        if (-not $Force) {
            Write-Error "TargetDir '$target' already exists and is not empty. Re-run with -Force to overwrite."
            exit 1
        }
        Write-Warning "TargetDir '$target' is not empty; -Force given — existing files may be overwritten."
    }
    New-Item -ItemType Directory -Path $target -Force | Out-Null
}

# --- Directories (allowlist) ---
Copy-TemplateDir ".claude/skills" -ExcludeRel @(".claude/skills/_legacy")
Copy-TemplateDir ".claude/commands"
Copy-TemplateDir "src"
Copy-TemplateDir "docs"
Copy-TemplateDir ".github"
if ($ExcludeSelf) {
    Copy-TemplateDir "scripts" -ExcludeRel @("scripts/export-template.ps1")
} else {
    Copy-TemplateDir "scripts"
}
# Optional template scaffolds (only if present)
Copy-TemplateDir "_template"
Copy-TemplateDir "positions/_template"   # the ONLY positions/ subtree ever exported

# --- Individual files (allowlist) ---
$files = @(
    "pyproject.toml",
    ".python-version",
    "README.md",
    ".gitignore",
    ".gitattributes",
    "claude-skills.lock",
    ".claude/README.md"
)
foreach ($f in $files) { Copy-TemplateFile $f }

# --- Every *.example.* file at the repo root and one level into .claude/ ---
Get-ChildItem -Path $repoRoot -File -Filter "*.example.*" | ForEach-Object {
    Copy-TemplateFile $_.Name
}
$claudeDir = Join-Path $repoRoot ".claude"
if (Test-Path $claudeDir) {
    Get-ChildItem -Path $claudeDir -File -Filter "*.example.*" | ForEach-Object {
        Copy-TemplateFile (".claude/" + $_.Name)
    }
}

Write-Host ""
Write-Host "=== Summary ===" -ForegroundColor Green
Write-Host "Copied:  $copyCount files"
Write-Host "Denied:  $skipCount files (candidate data / secrets)"
if ($DryRun) {
    Write-Host "(dry run — no files written)" -ForegroundColor Cyan
} else {
    Write-Host ""
    Write-Host "Next: start a FRESH git history in the template (do NOT clone this repo — its" -ForegroundColor Yellow
    Write-Host "history contains PII). See the .IMPORTANT note in this script's header." -ForegroundColor Yellow
    Write-Host "  cd `"$target`"; git init; git add -A; git commit -m `"Initial import: job-seeking template`""
}
