# Hebrew (and other right-to-left) support

| Area | What works |
|---|---|
| **Agent conversation** | The agent answers in the language you write in. The `onboard` interview is held in that language; `profile.json` field *values* (titles, keywords) can be Hebrew or English. |
| **Keyword matching** | A Hebrew keyword also matches with its attached prefix letters — `מפתח` matches `למפתח`, `המפתח`, `ב-מפתח`. It does **not** match a different word that merely starts with it (`מפתחות`). Add both forms to the profile when you want plurals (`מפתח`, `מפתחים`). |
| **Dashboard** | `board_server` sets `dir="auto"` per line, so Hebrew titles render right-to-left and English ones left-to-right in the same list. |
| **CV PDF** | A CV that is mostly Hebrew/Arabic is rendered through Chrome/Chromium/Edge (correct bidi + shaping). Without one, the tool says so instead of silently producing boxes; use `docx_export` and print from Word. |
| **Terminal** | Terminals often display Hebrew out of order. If the agent's reply looks garbled, ask it to "write that to a file" and open the file. |

## Tips
- Put `he` first in `locale.languages` in your profile if Hebrew is your working language, so the
  interview-prep research uses Hebrew sources too.
- Mixed-language CVs (Hebrew text with English tech terms) are fine; they are laid out RTL when
  Hebrew letters are at least 30% of the letters.
- Recruiter replies: the agent drafts in the language the recruiter wrote in.
