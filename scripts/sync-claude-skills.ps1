<#
.SYNOPSIS
Sync skills from claude-sdlc into the current project, governed by claude-skills.lock.

.DESCRIPTION
Reads claude-skills.lock in the current directory and copies each tracked skill's ENTIRE
directory (SKILL.md + companion files such as rules.md, references/) from the source
claude-sdlc repo's *git content* at a pinned commit — never from the working tree — into
.claude/skills/. Updates the lock file with the pinned commit SHA and a deterministic
content_sha256 integrity hash per skill.

Refuses to run if the source repo has uncommitted changes under .claude/skills/ (those
edits would be silently ignored since the copy comes from git content); pass -Force to
override. Skills marked local_modifications=true are skipped with a warning unless -Force
is passed. Use -Force only after manually re-applying your local modifications onto the
upstream version.

.PARAMETER LockFile
Path to the lock file. Default: claude-skills.lock

.PARAMETER DryRun
Show what would change without writing files.

.PARAMETER Force
Overwrite skills marked local_modifications=true AND ignore a dirty upstream .claude/skills/.

.PARAMETER At
Commit-ish in the source repo to sync from. Default: HEAD.

.PARAMETER NoClaudeMdCheck
Skip the post-sync check that compares lock-file skills against the project's
CLAUDE.md skills table. Default: check is enabled.

.EXAMPLE
.\scripts\sync-claude-skills.ps1
.\scripts\sync-claude-skills.ps1 -DryRun
.\scripts\sync-claude-skills.ps1 -At 1a2b3c4
.\scripts\sync-claude-skills.ps1 -Force
.\scripts\sync-claude-skills.ps1 -NoClaudeMdCheck

.NOTES
See docs/skills-sharing.md in claude-sdlc for the convention this script implements.
#>
[CmdletBinding()]
param(
    [string]$LockFile = "claude-skills.lock",
    [switch]$DryRun,
    [switch]$Force,
    [string]$At = "HEAD",
    [switch]$NoClaudeMdCheck
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $LockFile)) {
    Write-Error "Lock file not found: $LockFile (cwd: $PWD)"
    exit 1
}

$lock = Get-Content $LockFile -Raw | ConvertFrom-Json
$sourceDir = $lock.source_local_path

if (-not (Test-Path $sourceDir)) {
    Write-Error "Source repo not found at '$sourceDir'. Update source_local_path in $LockFile."
    exit 1
}

# Resolve the commit to sync from (pinned; never the working tree)
$syncSha = (git -C $sourceDir rev-parse --verify --quiet ($At + "^{commit}") 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $syncSha) {
    Write-Error "Cannot resolve '-At $At' to a commit in $sourceDir."
    exit 1
}
$syncSha = "$syncSha".Trim()
$sourceBranch = (git -C $sourceDir rev-parse --abbrev-ref HEAD).Trim()

if ($sourceBranch -ne $lock.default_branch) {
    Write-Warning "Source repo is on branch '$sourceBranch', expected '$($lock.default_branch)'. Continuing -- verify this is intentional."
}

# Refuse to run against a dirty upstream skills tree: the copy comes from git content,
# so uncommitted upstream edits would be silently ignored — surface that instead.
$dirty = @(git -C $sourceDir status --porcelain -- .claude/skills) | Where-Object { $_ }
if ($dirty.Count -gt 0) {
    if ($Force) {
        Write-Warning "Upstream .claude/skills/ has uncommitted changes; -Force given -- syncing from committed content at $syncSha (uncommitted edits are NOT copied)."
    } else {
        Write-Host "ERROR: upstream repo '$sourceDir' has uncommitted changes under .claude/skills/." -ForegroundColor Red
        Write-Host "Sync copies committed git content only, so those edits would be silently skipped." -ForegroundColor Red
        Write-Host "Commit (or stash) them upstream, or re-run with -Force to sync from $syncSha anyway." -ForegroundColor Red
        exit 1
    }
}

# Deterministic content hash over a skill directory:
# files sorted bytewise (ordinal) by relative path (forward slashes);
# digest input per file = <relative-path as UTF-8> + LF + <raw file bytes>.
# Must stay byte-identical to hash_skill_dir() in sync-claude-skills.sh.
function Get-SkillDirHash {
    param([string]$Dir)
    $root = (Resolve-Path $Dir).Path
    $rels = @(Get-ChildItem -Path $root -Recurse -File | ForEach-Object {
        $_.FullName.Substring($root.Length + 1) -replace '\\', '/'
    })
    [Array]::Sort($rels, [System.StringComparer]::Ordinal)
    $hasher = [System.Security.Cryptography.IncrementalHash]::CreateHash(
        [System.Security.Cryptography.HashAlgorithmName]::SHA256)
    foreach ($rel in $rels) {
        $hasher.AppendData([System.Text.Encoding]::UTF8.GetBytes($rel + "`n"))
        $hasher.AppendData([System.IO.File]::ReadAllBytes((Join-Path $root ($rel -replace '/', '\'))))
    }
    $digest = $hasher.GetHashAndReset()
    $hasher.Dispose()
    return (($digest | ForEach-Object { $_.ToString("x2") }) -join "")
}

$updated = @()
$skipped = @()
$missing = @()
$nowIso = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

foreach ($skillName in @($lock.skills.PSObject.Properties.Name)) {
    $entry = $lock.skills.$skillName
    $srcPath = ".claude/skills/$skillName"
    $dstDir  = ".claude\skills\$skillName"

    git -C $sourceDir cat-file -e ($syncSha + ":" + $srcPath + "/SKILL.md") 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[MISSING] $skillName -- $srcPath/SKILL.md not found at $syncSha in $sourceDir" -ForegroundColor Red
        $missing += $skillName
        continue
    }

    if ($entry.local_modifications -and -not $Force) {
        $reason = if ($entry.modification_reason) { $entry.modification_reason } else { "(no reason recorded)" }
        Write-Host "[SKIP-LOCAL] $skillName -- local_modifications=true. Reason: $reason" -ForegroundColor Yellow
        $skipped += $skillName
        continue
    }

    if ($DryRun) {
        Write-Host "[DRY] Would copy (from $syncSha):" -ForegroundColor Cyan
        git -C $sourceDir ls-tree -r --name-only $syncSha -- $srcPath | ForEach-Object {
            Write-Host "[DRY]   $_" -ForegroundColor Cyan
        }
        $updated += $skillName
        continue
    }

    # Extract the skill directory from git content via a zip archive (binary-safe;
    # avoids piping raw tar bytes through the PowerShell pipeline).
    $tmpZip = Join-Path ([System.IO.Path]::GetTempPath()) ("skill-sync-" + [guid]::NewGuid().ToString("N") + ".zip")
    $tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("skill-sync-" + [guid]::NewGuid().ToString("N"))
    try {
        git -C $sourceDir archive --format=zip --output="$tmpZip" $syncSha $srcPath
        if ($LASTEXITCODE -ne 0) { throw "git archive failed for $skillName at $syncSha" }
        Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force

        # Replace the destination directory with the upstream git content (whole dir,
        # so companion files removed upstream don't linger downstream).
        if (Test-Path $dstDir) {
            Remove-Item -Recurse -Force $dstDir
        }
        New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
        Copy-Item -Path (Join-Path $tmpDir ".claude\skills\$skillName\*") -Destination $dstDir -Recurse -Force
    } finally {
        if (Test-Path $tmpZip) { Remove-Item -Force $tmpZip }
        if (Test-Path $tmpDir) { Remove-Item -Recurse -Force $tmpDir }
    }

    $contentHash = Get-SkillDirHash -Dir $dstDir

    foreach ($kv in @(
        @{ k = "synced_from_commit"; v = $syncSha },
        @{ k = "synced_at";          v = $nowIso },
        @{ k = "content_sha256";     v = $contentHash }
    )) {
        $entry | Add-Member -NotePropertyName $kv.k -NotePropertyValue $kv.v -Force
    }
    Write-Host "[SYNCED] $skillName from $syncSha (content_sha256: $($contentHash.Substring(0,12))...)" -ForegroundColor Green
    $updated += $skillName
}

if (-not $DryRun -and $updated.Count -gt 0) {
    $lock.last_synced_at = $nowIso
    # Write JSON without BOM for cross-platform git compatibility
    $json = $lock | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText((Resolve-Path $LockFile).Path, $json, (New-Object System.Text.UTF8Encoding $false))
}

Write-Host ""
Write-Host "=== Summary ===" -ForegroundColor Green
Write-Host "Source: $sourceDir @ $syncSha (branch: $sourceBranch)"
Write-Host "Updated: $($updated.Count) skills"
Write-Host "Skipped: $($skipped.Count) skills (local modifications)"
Write-Host "Missing: $($missing.Count) skills (source file not found)"
if ($DryRun) { Write-Host "(dry run -- no files written)" -ForegroundColor Cyan }

# CLAUDE.md skills table check (advisory; never edits the file)
if (-not $NoClaudeMdCheck) {
    $claudeMdPath = $null
    foreach ($candidate in @("CLAUDE.md", ".claude\CLAUDE.md")) {
        if (Test-Path $candidate) {
            $content = Get-Content $candidate -Raw
            # Look for a markdown table cell containing a slash-prefixed skill name
            if ($content -match '\|\s*`/[\w-]+`') {
                $claudeMdPath = $candidate
                break
            }
        }
    }

    if ($claudeMdPath) {
        $claudeMdContent = Get-Content $claudeMdPath -Raw
        $undocumented = @()
        foreach ($skillName in @($lock.skills.PSObject.Properties.Name)) {
            $trigger = "/$skillName"
            # Match the trigger as a whole token (avoid /architect matching /aws-architect)
            if ($claudeMdContent -notmatch ('`' + [regex]::Escape($trigger) + '`')) {
                $undocumented += $skillName
            }
        }

        if ($undocumented.Count -gt 0) {
            Write-Host ""
            Write-Host "=== CLAUDE.md skills table check ===" -ForegroundColor Yellow
            Write-Host "Found skills table in $claudeMdPath, but these skills are NOT listed:"
            foreach ($skill in $undocumented) {
                $description = ""
                # Extract description from YAML frontmatter of the pinned git content
                $skillContent = (git -C $sourceDir show ($syncSha + ":.claude/skills/$skill/SKILL.md") 2>$null) -join "`n"
                if ($LASTEXITCODE -eq 0 -and $skillContent) {
                    if ($skillContent -match '(?ms)^---\s*$.*?^description:\s*["'']?(.+?)["'']?\s*$') {
                        $description = $Matches[1].Trim()
                        if ($description.Length -gt 80) {
                            $description = $description.Substring(0, 77) + "..."
                        }
                    }
                }
                Write-Host "  - /$skill"
                Write-Host "    Suggested row: | ``/$skill`` | <trigger> | $description |"
            }
            Write-Host "Add these rows to $claudeMdPath manually (table schema varies; auto-edit is unsafe)."
        }
    } else {
        Write-Host "(CLAUDE.md skills table not found -- skipping documentation check)" -ForegroundColor DarkGray
    }
}

if ($missing.Count -gt 0) { exit 2 }
