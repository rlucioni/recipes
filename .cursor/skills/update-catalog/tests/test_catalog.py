from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "catalog.py"
SPEC = importlib.util.spec_from_file_location("catalog_helper", SCRIPT)
assert SPEC and SPEC.loader
catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog)


class CatalogTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "recipes").mkdir()
        self.work = self.root / ".catalog-work"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def recipe(self, name: str, body: str = "# Recipe\n") -> None:
        (self.root / "recipes" / f"{name}.md").write_text(body, encoding="utf-8")

    def write_catalog(self, rows: list[dict[str, str]]) -> None:
        indexed = {row["name"]: row for row in rows}
        (self.root / "catalog.tsv").write_text(
            catalog.catalog_text(indexed), encoding="utf-8"
        )

    def row(
        self,
        name: str,
        recipe_type: str = "",
        prep_time: str = "",
    ) -> dict[str, str]:
        return {
            "name": name,
            "type": recipe_type,
            "prep_time": prep_time,
        }

    def write_state(self) -> None:
        recipes = catalog.discover(self.root)
        catalog.write_json(
            self.root / "catalog-state.json",
            {
                "schema_version": catalog.SCHEMA_VERSION,
                "recipe_hashes": {
                    name: recipes[name]["sha256"] for name in sorted(recipes)
                },
            },
        )

    def write_candidates(self, plan: dict, rows: dict[str, dict[str, str]]) -> None:
        for batch in plan["batches"]:
            lines = []
            for name in batch["names"]:
                row = rows[name]
                lines.append("\t".join(row[field] for field in catalog.HEADER))
            path = self.work / batch["candidate"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_validate_complete_catalog(self) -> None:
        self.recipe("alpha")
        self.recipe("beta")
        self.write_catalog(
            [
                self.row("alpha", "meal", "short"),
                self.row("beta", "drink", "medium"),
            ]
        )

        result = catalog.validate_catalog(self.root)

        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["rows"], 2)

    def test_validate_reports_schema_and_join_errors(self) -> None:
        self.recipe("alpha")
        (self.root / "catalog.tsv").write_text(
            "name\ttype\tprep_time\n"
            "beta\tinvalid\tweeknight\n",
            encoding="utf-8",
        )

        result = catalog.validate_catalog(self.root)

        self.assertFalse(result["ok"])
        text = "\n".join(result["errors"])
        self.assertIn("type is not allowed", text)
        self.assertIn("prep_time is not allowed", text)
        self.assertIn("missing recipe rows: alpha", text)
        self.assertIn("rows without live recipes: beta", text)

    def test_add_new_plan_batches_deterministically_and_applies(self) -> None:
        for name in ("alpha", "beta", "gamma"):
            self.recipe(name)
        self.write_catalog([self.row("alpha", "meal", "short")])
        catalog.write_json(
            self.root / "catalog-state.json",
            {
                "schema_version": catalog.SCHEMA_VERSION,
                "recipe_hashes": {
                    "alpha": catalog.discover(self.root)["alpha"]["sha256"]
                },
            },
        )

        plan = catalog.create_plan(self.root, "add-new", 1, self.work)

        self.assertEqual(plan["additions"], ["beta", "gamma"])
        self.assertEqual([batch["names"] for batch in plan["batches"]], [["beta"], ["gamma"]])
        candidates = {
            "beta": self.row("beta", "drink", "short"),
            "gamma": self.row("gamma", "component", "long"),
        }
        self.write_candidates(plan, candidates)
        catalog.load_candidates(self.work, plan)
        diff = catalog.apply_plan(self.root, self.work, dry_run=False)

        self.assertEqual([entry["name"] for entry in diff["added"]], ["beta", "gamma"])
        self.assertTrue(catalog.validate_catalog(self.root)["ok"])
        state = json.loads((self.root / "catalog-state.json").read_text())
        self.assertEqual(set(state["recipe_hashes"]), {"alpha", "beta", "gamma"})

    def test_add_new_does_not_mark_changed_existing_recipe_processed(self) -> None:
        self.recipe("alpha", "# Original\n")
        self.write_catalog([self.row("alpha", "meal")])
        self.write_state()
        old_hash = json.loads((self.root / "catalog-state.json").read_text())[
            "recipe_hashes"
        ]["alpha"]
        self.recipe("alpha", "# Changed\n")
        self.recipe("beta")

        plan = catalog.create_plan(self.root, "add-new", 10, self.work)
        self.write_candidates(plan, {"beta": self.row("beta", "meal")})
        catalog.apply_plan(self.root, self.work, dry_run=False)

        state = json.loads((self.root / "catalog-state.json").read_text())
        self.assertEqual(state["recipe_hashes"]["alpha"], old_hash)
        self.assertNotEqual(
            state["recipe_hashes"]["alpha"],
            catalog.discover(self.root)["alpha"]["sha256"],
        )

    def test_update_table_skips_unchanged_and_plans_changed(self) -> None:
        self.recipe("alpha", "# Original\n")
        self.recipe("beta")
        self.write_catalog([self.row("alpha", "meal"), self.row("beta", "drink")])
        self.write_state()

        first = catalog.create_plan(self.root, "update-table", 10, self.work)
        self.assertEqual(first["items"], [])

        self.recipe("alpha", "# Changed\n")
        second = catalog.create_plan(self.root, "update-table", 10, self.work)
        self.assertEqual([item["name"] for item in second["items"]], ["alpha"])
        self.assertIn("recipe_content_changed", second["items"][0]["reasons"])
        self.assertEqual(second["items"][0]["current"]["type"], "meal")

    def test_update_table_deletes_orphans(self) -> None:
        self.recipe("alpha")
        self.write_catalog([self.row("alpha", "meal"), self.row("retired", "meal")])
        self.write_state()

        plan = catalog.create_plan(self.root, "update-table", 10, self.work)
        self.assertEqual(plan["deletions"], ["retired"])
        self.assertEqual(plan["items"], [])
        diff = catalog.apply_plan(self.root, self.work, dry_run=False)

        self.assertEqual(diff["deleted"], [{"name": "retired"}])
        self.assertTrue(catalog.validate_catalog(self.root)["ok"])

    def test_schema_version_change_reconciles_all_rows(self) -> None:
        self.recipe("alpha")
        self.recipe("beta")
        self.write_catalog([self.row("alpha", "meal"), self.row("beta", "drink")])
        self.write_state()
        state = json.loads((self.root / "catalog-state.json").read_text())
        state["schema_version"] = catalog.SCHEMA_VERSION - 1
        catalog.write_json(self.root / "catalog-state.json", state)

        plan = catalog.create_plan(self.root, "update-table", 10, self.work)

        self.assertEqual([item["name"] for item in plan["items"]], ["alpha", "beta"])
        self.assertTrue(
            all("schema_version_changed" in item["reasons"] for item in plan["items"])
        )

    def test_header_drift_is_rewritten_without_classification(self) -> None:
        self.recipe("alpha")
        (self.root / "catalog.tsv").write_text(
            "name\ttype\n"
            "alpha\tmeal\n",
            encoding="utf-8",
        )
        self.write_state()

        plan = catalog.create_plan(self.root, "update-table", 10, self.work)
        self.assertTrue(plan["header_changed"])
        self.assertEqual(plan["items"], [])
        catalog.finish_plan(self.root, self.work, dry_run=False)

        header = (self.root / "catalog.tsv").read_text().splitlines()[0]
        self.assertEqual(header.split("\t"), catalog.HEADER)

    def test_candidate_validation_rejects_wrong_names_and_width(self) -> None:
        self.recipe("alpha")
        self.write_catalog([])
        plan = catalog.create_plan(self.root, "add-new", 10, self.work)
        candidate = self.work / plan["batches"][0]["candidate"]
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("wrong\tmeal\n", encoding="utf-8")

        with self.assertRaises(catalog.CatalogError):
            catalog.load_candidates(self.work, plan)

    def test_dry_run_writes_summary_but_not_catalog_or_state(self) -> None:
        self.recipe("alpha")
        self.write_catalog([])
        catalog.write_json(
            self.root / "catalog-state.json",
            {"schema_version": catalog.SCHEMA_VERSION, "recipe_hashes": {}},
        )
        before_catalog = (self.root / "catalog.tsv").read_text()
        before_state = (self.root / "catalog-state.json").read_text()
        plan = catalog.create_plan(self.root, "add-new", 10, self.work)
        self.write_candidates(plan, {"alpha": self.row("alpha", "meal")})

        diff = catalog.apply_plan(self.root, self.work, dry_run=True)

        self.assertTrue(diff["dry_run"])
        self.assertEqual((self.root / "catalog.tsv").read_text(), before_catalog)
        self.assertEqual((self.root / "catalog-state.json").read_text(), before_state)
        self.assertTrue((self.work / "summary.md").exists())

    def test_finish_applies_and_validates_in_one_step(self) -> None:
        self.recipe("alpha")
        self.write_catalog([])
        catalog.write_json(
            self.root / "catalog-state.json",
            {"schema_version": catalog.SCHEMA_VERSION, "recipe_hashes": {}},
        )
        plan = catalog.create_plan(self.root, "add-new", 10, self.work)
        self.write_candidates(plan, {"alpha": self.row("alpha", "meal")})

        result = catalog.finish_plan(self.root, self.work, dry_run=False)

        self.assertTrue(result["ok"])
        self.assertTrue(result["validation"]["ok"])
        self.assertEqual(result["validation"]["rows"], 1)

    def test_bootstrap_requires_a_valid_catalog(self) -> None:
        self.recipe("alpha")
        self.write_catalog([])
        with self.assertRaises(catalog.CatalogError):
            catalog.bootstrap_state(self.root, dry_run=False)


if __name__ == "__main__":
    unittest.main()
