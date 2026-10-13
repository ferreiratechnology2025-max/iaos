# Golden visual references

```
SOURCE=IAOS_UI_PROTOTYPE_V0_2
STATUS=FROZEN_REFERENCE
```

Canonical set (1440x900 viewport, light theme, prototype rendered with the fallback font stack):

- `home.png`
- `authority.png`
- `execution-silent-escape.png`
- `settings-models-local.png`
- `settings-models-cloud-failed.png`

Contract: palette, spacing, type hierarchy, sidebar, density, divider lines, status pills, Silent escape,
6-stage flow, Claim/Evidence/Verification, Authority and Model profiles must be preserved.
Accepted difference: font renders as Segoe UI on Windows (FONT_DIFFERENCE=ACCEPTED).
Any perceptible visual-language change must return to the UI gate; do not overwrite these files to make a diff pass.
Current renders of the real app: `npm run shoot` -> `shots/` (git-ignored).
