# recipes

Markdown recipe collection (adapted from books, shows, the internet). Prefer editing recipe files over `chefbot/` unless the task is about the Slack bot.

## Layout

- `recipes/` — live recipes (`*.md`), including authoritative metadata in YAML frontmatter.
- `archive/` — retired recipes. Do not treat as current or include in the catalog.
- `catalog.tsv` — generated metadata index for browsing, planning, and filtering. Never edit it as the source of truth; regenerate it with the `update-catalog` skill.
- `chefbot/` — Slack culinary assistant. It embeds `recipes/*.md`.
- `resources/` — supporting assets (charts, PDFs), not recipes.

## Recipe files

- Filename: kebab-case, matching the title (`posset.md`, `mapo-tofu.md`).
- Shape: YAML frontmatter, `# Title`, optional attribution/yield line, `## Ingredients`, `## Instructions`. No equipment lists. Prefer grams for dry goods when nearby recipes already do.
- Every live recipe begins with frontmatter using this shape. `leftoverability` and `protein` are present only for meals; `specialty_ingredients` is a YAML list.

```yaml
---
type: meal
prep_time: short
leftoverability: high
protein:
  - meat
specialty_ingredients:
  - other
---
```

## Frontmatter schema

- `type`: one of `meal`, `baked_or_dessert`, `drink`, `component`.
  - `meal`: a full meal or distinct dish, including soups and salads.
  - `baked_or_dessert`: breads, pastries, desserts, granola, and similar.
  - `drink`: any beverage, including coffee and tea.
  - `component`: intended for another recipe and not eaten alone, such as pie crust, dashi, sauces, or purees.
- `prep_time`: one of `short`, `medium`, `long`. Judge total practical effort: elapsed time, separate components, and cleanup.
  - Follow links to component recipes. Include a component's prep when it would be made as part of this recipe; making soft-boiled eggs counts.
  - Exclude components normally made in a large batch and stored; using prepared chili oil does not add its production time.
  - `short`: about 30 minutes or less.
  - `medium`: reasonable on a weeknight, without very long simmer/proof times or many separately prepared pieces. `caldo-verde` is `medium`.
  - `long`: not realistic on a weeknight; includes overnight proofing, many steps/components, and all deep frying. `shoyu-ramen` is `long`.
  - If unsure between adjacent values, choose the longer one.
- `leftoverability` (meals only): one of `low`, `medium`, `medium_with_prep`, `high`, `high_with_prep`. Omit for every other `type`.
 - `low`: leftovers are not worth eating; eat the day it is made (`baked-potato`, `grilled-cheese`, `somen`).
  - `medium`: still a reasonable next-day meal, but quality drops a bit after the day it is made because it becomes somewhat soggy, loses crispness, or spoils quickly (`cold-noodle-salad`, `crab-cakes`, `poke`). Also include dishes that keep well but are hard to scale (`chicken-pot-pie`).
  - `high`: keeps well for multiple days up to a week and may even improve with age. Can be easily made in large batches. Examples include chicken salad and most stews.
  - Use `_with_prep` only when cooked/prepared components keep well, save significant time, and still 
  require quick day-of assembly. `shoyu-ramen` qualifies. Do not use the suffix when leftovers can be 
  eaten directly from the refrigerator or only need microwaving.
- `protein` (meals only): a YAML list containing every applicable value from `meat`, `seafood`, `egg_dairy`, `plants`, `flexible`, `none`. Omit for every other `type`.
  - Tag every appreciable protein that is actually in the dish as written.
  - `none`: no appreciable protein (`baked-potato`). Cannot combine with another protein value.
  - `meat`: observable meat, including bacon, ham, sausage, and ground pork. Broth, fish sauce, oyster sauce, shrimp paste, and dashi do not count.
  - `seafood`: fish or shellfish eaten as food, not as seasoning.
  - `egg_dairy`: eggs, cheese, yogurt, and similar dairy protein.
  - `plants`: plant foods that are themselves protein sources (tofu, beans, lentils, corn, nuts). Ordinary vegetables, potatoes, rice, pasta, and bread do not qualify.
  - `flexible`: add when the written recipe allows swapping across `meat` / `seafood` / `plants` / `egg_dairy` (`pad-thai`). Meat-only swaps stay `meat`. Do not tag every `or` alternative; still tag proteins that are definitely present (`pad-thai` tofu is `plants` plus `flexible`).
- `specialty_ingredients`: a YAML list containing every applicable value from `none`, `seafood`, `meat`, `other`.
  - `none`: no special trip beyond my pantry and local grocery store.
  - Always treat fresh produce as locally available.
  - `seafood`: fresh seafood other than shrimp or non-sushi-grade salmon.
  - `meat`: products or cuts unavailable at an average high-end American grocery store, including pig trotters, ribs cut in half lengthwise, and duck. Most beef, pork, chicken, and sausage products are locally available.
  - `other`: any other specialty-store items such as curry paste, tamarind, and shrimp paste. Common herbs/spices, light and dark soy sauce, Chinese black vinegar, oyster sauce, Shaoxing wine, and somen noodles are already available; other Asian noodles require a trip.

## Agent conventions

- Recipe identity comes from the filename stem. The generated catalog derives its `name` column from the filename.
- When adding, editing metadata, renaming, or archiving a recipe, regenerate `catalog.tsv` using the `update-catalog` skill.
- When populating frontmatter interactively, ask the human about any uncertain field. Otherwise, prefer a reasonable value over omission unless the recipe is blank, incomplete, or contradictory.
- Before exploring or planning, run the `update-catalog` skill and read the catalog first. Open recipe files only when ingredients or method are needed.
