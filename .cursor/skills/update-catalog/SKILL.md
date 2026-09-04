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
printf 'name\ttype\tprep_time\tleftoverability\tprotein\tspecialty_ingredients\n' > "$tmp"

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
    function add_list(value, kind) {
      if (kind == "protein") {
        if (!allowed(value, "meat seafood egg_dairy plants flexible none")) {
          fail("invalid protein value: " value)
        }
        protein[++protein_count] = value
      } else {
        if (!allowed(value, "none seafood meat other")) {
          fail("invalid specialty_ingredients value: " value)
        }
        specialty[++specialty_count] = value
      }
    }
    function sort_list(arr, n,    i, j, value) {
      for (i = 1; i <= n; i++) {
        for (j = i + 1; j <= n; j++) {
          if (arr[j] < arr[i]) {
            value = arr[i]
            arr[i] = arr[j]
            arr[j] = value
          }
        }
      }
    }
    function join_list(arr, n,    i, result) {
      sort_list(arr, n)
      for (i = 1; i <= n; i++) {
        result = result (i == 1 ? "" : "|") arr[i]
      }
      return result
    }
    function reject_none_combo(arr, n, field) {
      if (n > 1) {
        for (i = 1; i <= n; i++) {
          if (arr[i] == "none") {
            fail("none cannot be combined with another " field " value")
          }
        }
      }
    }

    NR == 1 {
      if ($0 != "---") fail("missing YAML frontmatter")
      in_frontmatter = 1
      next
    }
    in_frontmatter && $0 == "---" {
      closed = 1
      if (!allowed(type, "meal baked_or_dessert drink component")) {
        fail("missing or invalid type")
      }
      if (!allowed(prep_time, "short medium long")) {
        fail("missing or invalid prep_time")
      }
      if (type == "meal" && !allowed(leftoverability, "low medium medium_with_prep high high_with_prep")) {
        fail("meal requires valid leftoverability")
      }
      if (type != "meal" && leftoverability_seen) {
        fail("leftoverability must be omitted for non-meals")
      }
      if (type == "meal" && protein_count == 0) {
        fail("meal requires protein")
      }
      if (type != "meal" && protein_count > 0) {
        fail("protein must be omitted for non-meals")
      }
      if (specialty_count == 0) fail("specialty_ingredients must not be empty")
      reject_none_combo(protein, protein_count, "protein")
      reject_none_combo(specialty, specialty_count, "specialty_ingredients")
      printf "%s\t%s\t%s\t%s\t%s\t%s\n", recipe_name, type, prep_time, leftoverability, join_list(protein, protein_count), join_list(specialty, specialty_count)
      exit
    }
    in_frontmatter {
      if ($0 ~ /^name:[[:space:]]*/) {
        fail("name must be omitted; catalog name comes from the filename")
      } else if ($0 ~ /^type:[[:space:]]*/) {
        type = $0
        sub(/^type:[[:space:]]*/, "", type)
      } else if ($0 ~ /^prep_time:[[:space:]]*/) {
        prep_time = $0
        sub(/^prep_time:[[:space:]]*/, "", prep_time)
      } else if ($0 ~ /^leftoverability:[[:space:]]*/) {
        leftoverability_seen = 1
        leftoverability = $0
        sub(/^leftoverability:[[:space:]]*/, "", leftoverability)
      } else if ($0 ~ /^protein:[[:space:]]*$/) {
        reading_protein = 1
        reading_specialties = 0
      } else if ($0 ~ /^specialty_ingredients:[[:space:]]*$/) {
        reading_specialties = 1
        reading_protein = 0
      } else if (reading_protein && $0 ~ /^[[:space:]]*-[[:space:]]+/) {
        value = $0
        sub(/^[[:space:]]*-[[:space:]]+/, "", value)
        add_list(value, "protein")
      } else if (reading_specialties && $0 ~ /^[[:space:]]*-[[:space:]]+/) {
        value = $0
        sub(/^[[:space:]]*-[[:space:]]+/, "", value)
        add_list(value, "specialty")
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
  printf 'name\ttype\tprep_time\tleftoverability\tprotein\tspecialty_ingredients\n'
  sed '1d' "$tmp" | LC_ALL=C sort -t '	' -k1,1
} > "${tmp}.sorted"
mv "${tmp}.sorted" catalog.tsv
rm -f "$tmp"
trap - EXIT
```

If the command fails, report its validation error and leave `catalog.tsv`
unchanged. If it succeeds, report the number of exported recipes. Do not edit
recipe files as part of this skill.
