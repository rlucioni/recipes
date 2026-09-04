---
name: update-catalog
description: >-
  Sync catalog.tsv with live recipes in recipes/. Use when adding new recipes
  to the catalog, reconciling the table after files or schema change, or when
  the user asks to update the catalog (add-new or update-table modes).
---

# Update catalog

Maintain `catalog.tsv` (repo root). It is the metadata index for planning. Recipe `.md` files stay free of frontmatter.

Default path: `catalog.tsv`. Live recipes: `recipes/*.md`. Ignore `archive/`.

Join key: `name` = recipe basename without `.md` (`recipes/posset.md` → `posset`).

## Run the workflow

Use the mode the user names. If they do not name one, ask which mode to use.

Run all commands from the repository root with this helper:

```bash
python3 .cursor/skills/update-catalog/scripts/catalog.py
```

1. Create a deterministic plan:

   ```bash
   python3 .cursor/skills/update-catalog/scripts/catalog.py plan --mode MODE
   ```

   `MODE` is `add-new` or `update-table`. The command writes
   `.catalog-work/plan.json` plus one `batch-NNN.json` manifest per batch.

2. Read `plan.json`. If `batches` is non-empty, launch one subagent per manifest
   in parallel. Give each subagent the instructions under **Classify a batch**.
   Do not invent alphabetical ranges; manifests are the exact work list.

3. Validate every candidate, apply the plan atomically, update
   `catalog-state.json`, and validate the finished catalog:

   ```bash
   python3 .cursor/skills/update-catalog/scripts/catalog.py finish
   ```

   Use `finish --dry-run` when the user asks to preview without writing.
   `validate-candidates`, `apply`, and `validate` remain available as separate
   troubleshooting commands, but routine runs should use `finish`.

4. Read `.catalog-work/summary.md` and give that summary to the user.

The scripts perform discovery, hashing, batching, schema validation, merging,
sorting, and summaries. Do not replace these steps with ad hoc shell/Python
commands. `catalog-state.json` stores recipe content hashes so unchanged,
schema-valid recipes are skipped on future `update-table` runs.

## Modes

### `add-new`

Add each live recipe with no matching `name` row. Never modify or delete an
existing row. Classify all metadata fields for each new recipe.

### `update-table`

Fully reconcile the table:

- Add live recipes with no row.
- Delete rows with no `recipes/{name}.md`; never add files from `archive/`.
- Reconcile recipes whose content hash changed.
- Repair invalid values and rewrite columns to the current schema.
- When a changed recipe does not contradict valid existing metadata, preserve
  that metadata. Do not refresh a human-filled value from a weaker guess.

## Classify a batch

For each manifest, tell the subagent:

1. Read the manifest and every recipe path listed in `items`.
2. Produce the manifest's `candidate_path`, relative to `.catalog-work/`.
3. Write no header. Write exactly one row per item, in manifest order, with
   three tab-separated columns: `name`, `type`, `prep_time`. Preserve a
   trailing empty `prep_time` column.
4. For `action: add`, infer all metadata under **Field schema**.
5. For `action: reconcile`, start from `current`. Change only fields listed in
   `fields_to_infer`, and preserve each valid current value unless the recipe
   clearly contradicts it. If the correct value is not clear, leave an invalid
   field blank; otherwise retain the current value.
6. Never edit recipe files, `catalog.tsv`, state, plans, or another batch.

## File format

- UTF-8 TSV, tab-separated, header row required, one row per live recipe.
- Column order must match **Field schema** exactly.
- No extra columns. No `#` comments.
- Blank cell = unknown. Do not write `unknown`, `n/a`, or `?`.
- Quote fields only if a value contains a tab or newline (should not happen).

Empty catalog is header-only (valid).

## Field schema

Every column is listed. Allowed values are closed unless noted. Values are case-sensitive and must match exactly.

| Field | Required | Type | Allowed values | How to fill |
| --- | --- | --- | --- | --- |
| `name` | yes | string | Stem of a file in `recipes/`. Pattern: lowercase kebab-case, no path, no `.md` (`posset`, `mapo-tofu`). Must be unique. | Always `{filename}` minus `.md`. Never a path, never `archive/`, never the markdown H1. |
| `type` | no | enum, single | `meal` `baked_or_dessert` `drink` `component` | One value. `meal`: a full meal or a distinct dish in a meal, including soups and salads. `baked_or_dessert`: breads, pastries, desserts, granola, and similar. `drink`: any beverage, including coffee and tea. `component`: meant for another recipe and not eaten on its own (pie crust, dashi, sauces, purees). If it could reasonably be served as its own plate, it is not `component`. If unsure, blank. |
| `prep_time` | no | enum, single | `short` `medium` `long` | Judge the whole practical effort, including elapsed time, number of components, and cleanup. `short`: could be done in about 30 minutes or less. `medium`: could reasonably be done on a weeknight; excludes very long simmer/proof times and recipes with many steps or separately prepared components. `caldo-verde.md` is `medium` because it mainly involves putting ingredients into one pot. `long`: not realistic on a weeknight; includes overnight proofing, many steps or components, and all deep-frying recipes because setting up and cleaning a frying station adds substantial hassle. `shoyu-ramen.md` is `long` because dashi, noodles, broth, eggs, and other pieces must be prepared separately. If unsure between adjacent values, use the more conservative (longer) value. |
| `leftoverability` | no | enum, single | `low` `medium` `medium_with_prep` `high` `high_with_prep` | Only classify `meal` recipes. First judge the finished dish: `low` = keeps very poorly and should be eaten the day it is made (`baked-potato`, `grilled-cheese`); `medium` = can be reheated, but deteriorates after the day it is made, limiting how much it can be scaled (including dishes that become soggy, such as `cold-noodle-salad`; lose a crispy exterior, such as crab cakes; or spoil quickly, such as `poke`); `high` = keeps well for multiple days up to a week and may improve with age (most soups and stews). Then add `_with_prep` to `medium` or `high` only when components can be prepared and stored ahead, that prep saves significant time, and day-of assembly is quick. Cooking counts as significant prep; chopping vegetables alone does not. Quick assembly includes boiling noodles or assembling a taco. Example: `shoyu-ramen` uses a `_with_prep` value because its broth and toppings keep well, while the noodles can be freshly boiled day-of. |
| `specialty_ingredients` | no | enum, multiple | `none` `seafood` `meat` `pantry` `other` | Select every category that requires a special shopping trip beyond my pantry and local grocery store. Use `none` when no special trip is needed. Always treat fresh produce as locally available. `seafood`: fresh seafood other than shrimp or non-sushi-grade salmon. `meat`: meat products or cuts unavailable at an average high-end American grocery store, including pig trotters, ribs cut in half lengthwise, and duck; assume most beef, pork, chicken, and sausage products are locally available. `pantry`: specialty-store pantry items, including curry paste, tamarind, and shrimp paste. Assume common herbs and spices, light and dark soy sauce, Chinese black vinegar, oyster sauce, Shaoxing wine, and somen noodles are already available; other Asian noodles require a trip to an Asian grocery store. `other`: a specialty ingredient not covered by `seafood`, `meat`, or `pantry`. |

### Inference rules

1. Read the recipe file before filling anything besides `name`.
2. Prefer a guess over blank unless extremely unclear (e.g. the recipe file is blank, incomplete, or contradictory).
3. Never copy values from similarly named recipes.
4. Never edit the `.md` files themselves as part of this skill. If they contain contradictions, default to leaving fields blank and flag the contradiction in the report.
5. Sort rows by `name` A–Z after edits.

## Maintenance

The human-readable schema above and validation constants in
`scripts/catalog.py` must change together. Increment `SCHEMA_VERSION` in the
script whenever allowed columns, enums, or semantics change. A version change
causes `update-table` to reconcile existing rows once.

Use `bootstrap-state` only to initialize hashes for an already complete,
validated catalog:

```bash
python3 .cursor/skills/update-catalog/scripts/catalog.py bootstrap-state
```
