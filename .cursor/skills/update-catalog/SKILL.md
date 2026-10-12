---
name: update-catalog
description: >-
  Regenerate catalog.tsv from YAML frontmatter in live recipe files. Use after
  adding, editing, renaming, or archiving recipes, or when the user asks to
  update the catalog.
---

# Update catalog

`recipes/*.md` frontmatter is the metadata source of truth. Regenerate the
root-level `catalog.tsv` mechanically; never infer, classify, or revise recipe
metadata while using this skill. Derive the catalog's `name` column from each
filename stem; frontmatter must not contain `name`. Ignore `archive/`.

Run this from the repository root:

```bash
scripts/update-catalog.sh
```

The same script runs in the `deploy` GitHub Actions workflow on every push to
`master`, so the committed catalog catches up even if this step is skipped.

If the command fails, report its validation error and leave `catalog.tsv`
unchanged. If it succeeds, report the number of exported recipes. Do not edit
recipe files as part of this skill.
