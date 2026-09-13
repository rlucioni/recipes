---
name: archive-recipe
description: >-
  Move one or more live recipes to archive/ and regenerate catalog.tsv.
  Use when the user asks to archive, retire, or remove a recipe from the
  live collection.
---

# Archive recipe

Retire live `recipes/*.md` files into `archive/`. Do not edit recipe
content. Do not delete files. Do not commit unless asked.

## Resolve

Accept a single filename stem, a title, an open file, or a list. Map
each item to `recipes/<stem>.md`. Recipe identity is the filename stem.

If no recipes are named, ask which stems to archive.

If a name is ambiguous or `recipes/<stem>.md` is missing, stop and ask;
do not archive the rest yet. Refuse if `archive/<stem>.md` already exists.

## Incoming links

Before moving anything, search the repo for markdown links to each
`<stem>.md` (including `./<stem>.md` and `recipes/<stem>.md`). Ignore
the file being archived.

If any other recipe links to a candidate, stop. Do not move that file
or any other file in the batch. Warn with the stem and the linking
filenames, and wait for the user.

## Move

From the repository root, move each resolved file. Use `git mv` when
the file is tracked, otherwise `mv`:

```bash
git mv "recipes/<stem>.md" "archive/<stem>.md"
```

Keep the filename. Do not overwrite. Create `archive/` only if missing.

## Catalog

After every requested file has moved, run the `update-catalog` skill.
Report the stems that were archived and the new catalog count.
