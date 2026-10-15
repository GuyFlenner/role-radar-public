# Claude Code — project settings

| File | Purpose |
|------|---------|
| **`settings.local.json`** | Your **personal** overrides for this repo only. **Not committed** (see root `.gitignore`). |
| **`settings.local.json.example`** | Safe template; copy to `settings.local.json` and adjust. |

Precedence (highest first): managed → CLI → **local** → project `.claude/settings.json` → user `~/.claude/settings.json`.

Add team-shared rules in **`.claude/settings.json`** (tracked). Use **`settings.local.json`** for machine-specific paths, model choice, or extra `permissions.allow` / `permissions.deny` rules.

See [Claude Code settings](https://code.claude.com/docs/en/settings).
