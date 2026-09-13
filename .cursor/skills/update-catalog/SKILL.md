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

Run this single shell block from the repository root:

```bash
set -euo pipefail

tmp="$(mktemp "${TMPDIR:-/tmp}/catalog.XXXXXX")"
trap 'rm -f "$tmp" "${tmp}.sorted"' EXIT
printf 'name\tcourse\tprep_time\tleftoverability\tspecialty_ingredients\n' > "$tmp"

for file in recipes/*.md; do
  stem="$(basename "$file" .md)"
  awk -v recipe_name="$stem" '
    function fail(message) {
      print FILENAME ": " message > "/dev/stderr"
      exit 1
    }
    function allowed(value, choices, count, i) {
      count = split(choices, values, " ")
      for (i = 1; i <= count; i++) {
        if (value == values[i]) return 1
      }
      return 0
    }
    function add_specialty(value) {
      if (!allowed(value, "seafood meat other")) {
        fail("invalid specialty_ingredients value: " value)
      }
      specialty[++specialty_count] = value
    }
    function sorted_specialties(    i, j, value, result) {
      for (i = 1; i <= specialty_count; i++) {
        for (j = i + 1; j <= specialty_count; j++) {
          if (specialty[j] < specialty[i]) {
            value = specialty[i]
            specialty[i] = specialty[j]
            specialty[j] = value
          }
        }
      }
      for (i = 1; i <= specialty_count; i++) {
        result = result (i == 1 ? "" : "|") specialty[i]
      }
      return result
    }

    NR == 1 {
      if ($0 != "---") fail("missing YAML frontmatter")
      in_frontmatter = 1
      next
    }
    in_frontmatter && $0 == "---" {
      closed = 1
      if (!allowed(course, "breakfast main side snack component bread dessert drink")) {
        fail("missing or invalid course")
      }
      if (!allowed(prep_time, "short medium long")) {
        fail("missing or invalid prep_time")
      }
      if (course == "main" && !allowed(leftoverability, "low medium medium_with_prep high high_with_prep")) {
        fail("main requires valid leftoverability")
      }
      if (course != "main" && leftoverability_seen) {
        fail("leftoverability must be omitted for non-mains")
      }
      printf "%s\t%s\t%s\t%s\t%s\n", recipe_name, course, prep_time, leftoverability, sorted_specialties()
      exit
    }
    in_frontmatter {
      if ($0 ~ /^name:[[:space:]]*/) {
        fail("name must be omitted; catalog name comes from the filename")
      } else if ($0 ~ /^course:[[:space:]]*/) {
        course = $0
        sub(/^course:[[:space:]]*/, "", course)
      } else if ($0 ~ /^prep_time:[[:space:]]*/) {
        prep_time = $0
        sub(/^prep_time:[[:space:]]*/, "", prep_time)
      } else if ($0 ~ /^leftoverability:[[:space:]]*/) {
        leftoverability_seen = 1
        leftoverability = $0
        sub(/^leftoverability:[[:space:]]*/, "", leftoverability)
      } else if ($0 ~ /^specialty_ingredients:[[:space:]]*\[\][[:space:]]*$/) {
        reading_specialties = 0
      } else if ($0 ~ /^specialty_ingredients:[[:space:]]*$/) {
        reading_specialties = 1
      } else if (reading_specialties && $0 ~ /^[[:space:]]*-[[:space:]]+/) {
        value = $0
        sub(/^[[:space:]]*-[[:space:]]+/, "", value)
        add_specialty(value)
      } else if ($0 !~ /^[[:space:]]*$/) {
        fail("unsupported frontmatter line: " $0)
      }
      next
    }
    END {
      if (!closed) fail("unterminated YAML frontmatter")
    }
  ' "$file" >> "$tmp"
done

{
  printf 'name\tcourse\tprep_time\tleftoverability\tspecialty_ingredients\n'
  sed '1d' "$tmp" | LC_ALL=C sort -t '	' -k1,1
} > "${tmp}.sorted"
mv "${tmp}.sorted" catalog.tsv
rm -f "$tmp"
trap - EXIT
```

If the command fails, report its validation error and leave `catalog.tsv`
unchanged. If it succeeds, report the number of exported recipes. Do not edit
recipe files as part of this skill.
