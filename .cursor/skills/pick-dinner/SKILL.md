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
2. Keep rows where `type` is `meal`. Drop any `name` on the not-dinner list below.
3. If a remaining meal is clearly breakfast, a snack, or a side and is not on
   the list, skip it and add its filename stem to the list in this file in the
   same turn.

## Not-dinner list

Breakfast, snacks, and sides (filename stems):

- asparagus-salad
- baked-potato
- braised-green-beans
- braised-red-cabbage
- breakfast-burritos
- brussels-sprouts
- caramel-popcorn
- charred-street-corn
- chestnuts
- chia-pudding
- corn-tomato-and-avocado-salad
- cumin-potatoes
- deviled-eggs
- fried-brussels-sprouts
- fried-eggs
- garlic-bread
- garlic-knots
- garlic-mac-salad
- garlic-naan
- garlic-rice
- hard-boiled-eggs
- hash-browns
- mashed-potato-squash
- mashed-potatoes
- mustard-slaw
- oatmeal
- oven-fries
- pancakes
- poached-eggs
- polenta
- popcorn
- potato-salad
- roasted-beets
- roasted-garlic
- roasted-potatoes
- roasted-pumpkin-seeds
- roasted-vegetable
- sauteed-mushrooms
- smashed-cucumber-salad
- soft-boiled-eggs
- spaetzle
- squash-pancakes
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

## Rank

After hard filters (dinner + effort; leftovers only if they chose a
preference; ingredient matches preferred), **rank** remaining recipes.

- If effort is `short` or `medium` (including weeknight / low or medium
  effort), downweight meals whose `specialty_ingredients` is not `none`.
  Do not drop them unless the user asked to avoid a special trip.
- Do not drop a recipe only because it is off-season. A strong
  leftover-ingredient match can outrank season and specialty downweight.

Use today's date, Northern Hemisphere, US produce:

- **Spring** (Mar–May): asparagus, peas, lamb, lighter braises
- **Summer** (Jun–Aug): tomato, corn, zucchini, eggplant, cold noodles, gazpacho-style
- **Fall** (Sep–Nov): squash, apple, mushroom, cabbage, chili
- **Winter** (Dec–Feb): stews, braises, citrus, roots, hearty soups

Prefer in-season produce and weather-appropriate dishes. Open a recipe only
when ingredients or method are needed to judge season or an ingredient match.

## Suggest

Propose **3** recipes. For each, give the filename stem and one sentence
covering why it fits (effort, leftovers, ingredient, and/or season).

## Slim pickings

If fewer than 3 recipes survive the hard filters, say the pool is thin, list
whatever remains, and suggest 2–3 dinners **not in this repo** that fit the
same answers. Do not add those ideas to the collection unless asked.
