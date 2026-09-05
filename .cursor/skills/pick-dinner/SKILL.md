---
name: pick-dinner
description: >-
  Suggest three dinner recipes from this collection. Use when the user asks
  what to make for dinner, wants a dinner picker, or wants a shortlist of
  meals given time, leftovers, leftover ingredients, or the season.
---

# Pick dinner

Help pick tonight's dinner from live `recipes/*.md` via `catalog.tsv`. Do not
add recipes to the collection unless asked.

## Setup

1. Run the `update-catalog` skill, then read `catalog.tsv`.
2. Keep rows where `type` is `meal`. Drop any `name` on the breakfast,
   snack, or sides lists below.
3. If a remaining meal is clearly breakfast, a snack, or a side and is
   missing from those lists, skip it and add its filename stem to the
   matching list in this file in the same turn.

## Breakfast and snacks

- breakfast-burritos
- caramel-popcorn
- chestnuts
- chia-pudding
- deviled-eggs
- fried-eggs
- hard-boiled-eggs
- hash-browns
- oatmeal
- pan-con-tomate
- pancakes
- poached-eggs
- popcorn
- roasted-pumpkin-seeds
- soft-boiled-eggs
- squash-pancakes

## Sides

- asparagus-salad
- baked-potato
- braised-green-beans
- braised-red-cabbage
- brussels-sprouts
- charred-street-corn
- corn-tomato-and-avocado-salad
- cumin-potatoes
- fried-brussels-sprouts
- garlic-bread
- garlic-knots
- garlic-mac-salad
- garlic-naan
- garlic-rice
- mashed-potato-squash
- mashed-potatoes
- mustard-slaw
- oven-fries
- polenta
- potato-salad
- roasted-beets
- roasted-garlic
- roasted-potatoes
- roasted-vegetable
- sauteed-mushrooms
- smashed-cucumber-salad
- spaetzle
- steamed-artichokes
- sweet-potato-oven-fries
- sweet-potato-rice
- yuca-fries

## Questions

Ask these three in order. Use AskQuestion when available.

1. **Effort:** `short`, `medium`, `long`, or any. Map to catalog `prep_time`.
   "Weeknight" and "low or medium effort" mean `short` or `medium`.
2. **Leftovers:** `low` (tonight only), `medium` (fine tomorrow), `high`
   (several days), or any. Treat `medium_with_prep` as `medium` and
   `high_with_prep` as `high`. If the user says they don't care, any,
   or no preference, do not hard-filter on leftoverability.
3. **Ingredients to use up:** free text, optional. If given, open candidate
   recipe files and prefer dishes that use those ingredients.

## Weight

After hard filters (dinner + effort; leftovers only if they chose a
preference), give every remaining recipe a weight. Start at `1` and
**multiply**:

| Factor | Multiplier |
| leftover-ingredient strong match | ×4 |
| leftover-ingredient partial match | ×2 |
| in-season produce or weather-appropriate | ×2 |
| clearly off-season produce-forward | ×0.5 |
| `specialty_ingredients` is not `none` and effort is `short` or `medium` (including weeknight / low or medium) | ×0.25 |

Do not drop a recipe only because it is off-season or needs a specialty
trip, unless the user asked to avoid a special trip. Floor any weight
at `0.05`. Open a recipe only when ingredients or method are needed to
judge season or an ingredient match.

Use today's date, Northern Hemisphere, US produce:

- **Spring** (Mar–May): asparagus, peas, lamb, lighter braises
- **Summer** (Jun–Aug): tomato, corn, zucchini, eggplant, cold dishes
- **Fall** (Sep–Nov): squash, apple, mushroom, cabbage, chili
- **Winter** (Dec–Feb): stews, braises, citrus, roots, hearty soups

## Sample

Do not pick by preference or always take the highest weights. Pipe
`stem<TAB>weight` lines into this sampler and walk its output order
for the rest of the turn:

```bash
python3 -c '
import random, sys
items = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    stem, w = line.split("\t", 1)
    w = max(float(w), 0.05)
    items.append((random.random() ** (1.0 / w), stem))
items.sort(reverse=True)
for _, stem in items:
    print(stem)
'
```

## Suggest

Walk the sampled dinner list in batches of **3**. For each recipe, give
the filename stem and one sentence covering why it fits (effort,
leftovers, ingredient, and/or season).

After each batch, use AskQuestion when available: the three options
plus **None of these**. Do not repeat a recipe already shown.

- If they pick a recipe, stop paging. Then ask if they want side
  suggestions. Follow **Side suggestions** only if they say yes.
- If they pick None of these, show the next 3 from the sampled list.
- If the list runs out, say so. If fewer than 3 remain in a batch,
  show whatever is left plus None of these.

## Side suggestions

Only after the user asks for sides. Suggest **1–3** from the sides list.

Apply the same hard filters as dinner. Then **filter** by season: keep
year-round sides; drop sides built around clearly off-season produce.
Skip sides that repeat the main (another potato dish with a
potato-forward dinner, rice with fried rice, bread with a sandwich).

Weight the rest from `1` with the dinner multipliers, then also:

| Factor | Multiplier |
| complements well (contrast: greens/slaw with a starch-heavy main; starch with soup, stew, or chili) | ×4 |
| plausible pairing | ×1 |
| poor pairing | skip |

Open the chosen recipe and candidate sides when needed to judge the
pairing. Sample with the same sampler; take the first 1–3. For each,
give the filename stem and one sentence on why it fits.

## Slim pickings

If fewer than 3 dinners survive the hard filters, or the user rejects
the whole sampled list, say the pool is thin, list whatever remains
unshown, and suggest 2–3 dinners **not in this repo** that fit the
same answers. Do not add those ideas to the collection unless asked.
