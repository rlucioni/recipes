# recipes

Markdown recipe collection (adapted from books, shows, the internet). Prefer editing recipe files over `chefbot/` unless the task is about the Slack bot.

## Layout

- `recipes/` — live recipes (`*.md`). This is the source of truth for recipes.
- `archive/` — retired recipes. Do not treat as current. Do not add archive files to the catalog.
- `catalog.tsv` — metadata index for planning and filtering (type and practical prep time). Join key is `name` (filename minus `.md`). See `.cursor/skills/update-catalog/SKILL.md` for the field schema.
- `catalog-state.json` — content hashes used by `update-catalog` to skip unchanged recipes. Update it through the skill's helper, not by hand.
- `chefbot/` — Slack culinary assistant. It embeds `recipes/*.md` and should keep using that markdown shape (no YAML frontmatter in the stored recipe files).
- `resources/` — supporting assets (charts, PDFs), not recipes.

## Recipe files

- Filename: kebab-case, matching the title (`posset.md`, `mapo-tofu.md`).
- Shape: `# Title`, optional attribution/yield line, `## Ingredients`, `## Instructions`. No equipment lists. Prefer grams for dry goods when nearby recipes already do.
- Do not add YAML frontmatter to recipe files. Planning metadata lives in `catalog.tsv` only.

## Agent conventions

- When adding, renaming, or archiving a recipe, update `catalog.tsv` using the `update-catalog` skill.
- Use `python3 .cursor/skills/update-catalog/scripts/catalog.py` for catalog discovery, batching, validation, and merging; do not recreate those operations with ad hoc commands.
- Blank catalog cells mean unknown. Do not invent type or prep time.
- Explore and plan from the catalog first; open recipe files only for ingredients and method.
