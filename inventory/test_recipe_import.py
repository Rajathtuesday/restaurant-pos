# inventory/test_recipe_import.py
"""Tests for the AI recipe importer: the colloquial-unit parser, the shared
recipe_service write path, and the full job lifecycle (start -> review ->
confirm/discard), including the atomicity and IDOR guarantees called out
as non-negotiable in the approved plan."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from tenants.models import Tenant, Outlet, TenantFeatureOverride
from menu.models import MenuCategory, MenuItem
from inventory.models import InventoryItem, Recipe, RecipeImportJob, RecipeImportLine


class RecipeUnitTableTests(TestCase):
    """Pure tests of inventory.recipe_unit_table — no DB involved."""

    def test_native_unit_trusted_directly(self):
        from inventory.recipe_unit_table import parse_quantity_text
        qty, unit, needs_manual = parse_quantity_text("200g")
        self.assertEqual(qty, Decimal("200"))
        self.assertEqual(unit, "g")
        self.assertFalse(needs_manual)

    def test_native_unit_with_decimal_and_space(self):
        from inventory.recipe_unit_table import parse_quantity_text
        qty, unit, needs_manual = parse_quantity_text("1.5 kg")
        self.assertEqual(qty, Decimal("1.5"))
        self.assertEqual(unit, "kg")
        self.assertFalse(needs_manual)

    def test_colloquial_cup_converts_to_ml(self):
        from inventory.recipe_unit_table import parse_quantity_text
        qty, unit, needs_manual = parse_quantity_text("1 cup")
        self.assertEqual(qty, Decimal("240"))
        self.assertEqual(unit, "ml")
        self.assertFalse(needs_manual)

    def test_colloquial_tbsp_converts_to_ml(self):
        from inventory.recipe_unit_table import parse_quantity_text
        qty, unit, needs_manual = parse_quantity_text("2 tbsp")
        self.assertEqual(qty, Decimal("30"))
        self.assertEqual(unit, "ml")
        self.assertFalse(needs_manual)

    def test_ambiguous_phrase_to_taste_flags_manual(self):
        from inventory.recipe_unit_table import parse_quantity_text
        qty, unit, needs_manual = parse_quantity_text("Salt to taste")
        self.assertIsNone(qty)
        self.assertIsNone(unit)
        self.assertTrue(needs_manual)

    def test_ambiguous_phrase_a_pinch_flags_manual(self):
        from inventory.recipe_unit_table import parse_quantity_text
        _, _, needs_manual = parse_quantity_text("a pinch")
        self.assertTrue(needs_manual)

    def test_range_is_never_averaged(self):
        from inventory.recipe_unit_table import parse_quantity_text
        qty, unit, needs_manual = parse_quantity_text("200-250g")
        self.assertIsNone(qty)
        self.assertTrue(needs_manual)

    def test_unrecognized_unit_flags_manual_not_guessed(self):
        from inventory.recipe_unit_table import parse_quantity_text
        _, _, needs_manual = parse_quantity_text("2 medium")
        self.assertTrue(needs_manual)

    def test_unicode_fraction(self):
        from inventory.recipe_unit_table import parse_quantity_text
        qty, unit, needs_manual = parse_quantity_text("½ cup")
        self.assertEqual(qty, Decimal("120"))
        self.assertEqual(unit, "ml")
        self.assertFalse(needs_manual)

    def test_mixed_whole_and_fraction(self):
        from inventory.recipe_unit_table import parse_quantity_text
        qty, unit, needs_manual = parse_quantity_text("1 1/2 tsp")
        self.assertEqual(qty, Decimal("7.5"))
        self.assertEqual(unit, "ml")
        self.assertFalse(needs_manual)

    def test_pcs_synonym(self):
        from inventory.recipe_unit_table import parse_quantity_text
        qty, unit, needs_manual = parse_quantity_text("3 pieces")
        self.assertEqual(qty, Decimal("3"))
        self.assertEqual(unit, "pcs")
        self.assertFalse(needs_manual)

    def test_empty_string_flags_manual(self):
        from inventory.recipe_unit_table import parse_quantity_text
        _, _, needs_manual = parse_quantity_text("")
        self.assertTrue(needs_manual)

    def test_zero_quantity_flags_manual(self):
        from inventory.recipe_unit_table import parse_quantity_text
        _, _, needs_manual = parse_quantity_text("0g")
        self.assertTrue(needs_manual)


class RecipeServiceTests(TestCase):
    """recipe_service.upsert_recipe / upsert_modifier_recipe — the single
    validated write path shared by the manual UI and the AI importer."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Recipe Service Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.other_tenant = Tenant.objects.create(name="Other Tenant")
        self.other_outlet = Outlet.objects.create(tenant=self.other_tenant, name="Other Main")

        cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.menu_item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=cat, name="Curry", price=Decimal("200"),
        )
        self.onion = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Onion", unit="g", stock=Decimal("1000"),
        )
        self.foreign_item = InventoryItem.objects.create(
            tenant=self.other_tenant, outlet=self.other_outlet,
            name="Foreign Item", unit="g", stock=Decimal("100"),
        )

    def test_create_new_recipe_defaults_unit_from_inventory_item(self):
        from inventory.recipe_service import upsert_recipe
        recipe, created = upsert_recipe(self.menu_item, self.onion, Decimal("50"))
        self.assertTrue(created)
        self.assertEqual(recipe.unit, "g")
        self.assertEqual(recipe.quantity_required, Decimal("50"))

    def test_update_existing_recipe_preserves_unit_when_not_posted(self):
        from inventory.recipe_service import upsert_recipe
        Recipe.objects.create(
            menu_item=self.menu_item, inventory_item=self.onion,
            quantity_required=Decimal("10"), unit="kg",
        )
        recipe, created = upsert_recipe(self.menu_item, self.onion, Decimal("20"))
        self.assertFalse(created)
        self.assertEqual(recipe.unit, "kg")
        self.assertEqual(recipe.quantity_required, Decimal("20"))

    def test_incompatible_unit_raises_and_writes_nothing(self):
        from inventory.recipe_service import upsert_recipe, RecipeUnitMismatchError
        with self.assertRaises(RecipeUnitMismatchError):
            upsert_recipe(self.menu_item, self.onion, Decimal("2"), unit="ml")
        self.assertFalse(Recipe.objects.filter(menu_item=self.menu_item, inventory_item=self.onion).exists())

    def test_cross_tenant_raises(self):
        from inventory.recipe_service import upsert_recipe, RecipeCrossTenantError
        with self.assertRaises(RecipeCrossTenantError):
            upsert_recipe(self.menu_item, self.foreign_item, Decimal("10"))

    def test_modifier_recipe_incompatible_unit_raises(self):
        from inventory.recipe_service import upsert_modifier_recipe, RecipeUnitMismatchError
        from menu.models import ModifierGroup, Modifier
        group = ModifierGroup.objects.create(tenant=self.tenant, outlet=self.outlet, name="Extras")
        modifier = Modifier.objects.create(group=group, name="Extra Onion", price=Decimal("10"))
        with self.assertRaises(RecipeUnitMismatchError):
            upsert_modifier_recipe(modifier, self.onion, Decimal("10"), unit="ml")

    def test_modifier_recipe_cross_tenant_raises(self):
        from inventory.recipe_service import upsert_modifier_recipe, RecipeCrossTenantError
        from menu.models import ModifierGroup, Modifier
        group = ModifierGroup.objects.create(tenant=self.tenant, outlet=self.outlet, name="Extras")
        modifier = Modifier.objects.create(group=group, name="Extra Onion", price=Decimal("10"))
        with self.assertRaises(RecipeCrossTenantError):
            upsert_modifier_recipe(modifier, self.foreign_item, Decimal("10"))


def _run_task_synchronously(**kwargs):
    """Runs the real Celery task in-process, bypassing the broker — same
    mechanism recipe_import_start already falls back to when Celery is
    unreachable, just forced here so tests are deterministic either way."""
    from inventory.tasks import ai_import_recipe
    return ai_import_recipe(kwargs["job_id"], kwargs["text"], kwargs["image_b64"], kwargs["mime_type"])


class RecipeImportJobLifecycleTests(TestCase):
    """Integration tests: start -> extraction/matching -> review -> confirm
    or discard, against a real DB with AIService mocked out."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Lifecycle Tenant", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="ai_recipe_import", enabled=True)

        User = get_user_model()
        self.owner = User.objects.create_user(
            username="recipe_import_owner", password="pwd",
            role="owner", tenant=self.tenant, outlet=self.outlet,
        )
        cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.menu_item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=cat, name="Chicken Curry", price=Decimal("300"),
        )
        self.onion = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Onion", unit="g", stock=Decimal("5000"),
        )
        self.salt = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Salt", unit="g", stock=Decimal("2000"),
        )
        self.milk = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Milk", unit="ml", stock=Decimal("3000"),
        )

        self.client = Client()
        self.client.force_login(self.owner)

    def _start_job(self):
        return self.client.post(
            reverse("recipe-import-start"),
            data={"menu_item": self.menu_item.id, "text": "dummy recipe text"},
        )

    @patch("inventory.tasks.ai_import_recipe.delay")
    @patch("core.ai_service.AIService.match_ingredients")
    @patch("core.ai_service.AIService.parse_recipe")
    def test_high_confidence_lines_are_saved_low_confidence_lines_are_not(
        self, mock_parse_recipe, mock_match_ingredients, mock_delay
    ):
        mock_delay.side_effect = _run_task_synchronously
        mock_parse_recipe.return_value = [
            {"ingredient": "Onion", "quantity_text": "200g"},   # exact match, clean quantity
            {"ingredient": "Salt", "quantity_text": "to taste"},  # exact match, ambiguous quantity
            {"ingredient": "Chicken", "quantity_text": "500g"},   # no match at all
        ]
        mock_match_ingredients.return_value = {"Chicken": None}

        resp = self._start_job()
        self.assertEqual(resp.status_code, 200)
        job_id = resp.json()["job_id"]

        job = RecipeImportJob.objects.get(id=job_id)
        self.assertEqual(job.status, "ready_for_review")
        self.assertEqual(job.lines.count(), 3)

        onion_line = job.lines.get(raw_ingredient_name="Onion")
        self.assertEqual(onion_line.match_method, "exact")
        self.assertTrue(onion_line.include_in_recipe)
        self.assertEqual(onion_line.resolved_inventory_item, self.onion)

        salt_line = job.lines.get(raw_ingredient_name="Salt")
        self.assertTrue(salt_line.needs_manual_quantity)
        self.assertFalse(salt_line.include_in_recipe)

        chicken_line = job.lines.get(raw_ingredient_name="Chicken")
        self.assertEqual(chicken_line.match_method, "new")
        self.assertTrue(chicken_line.is_new_ingredient)
        self.assertFalse(chicken_line.include_in_recipe)

        # Confirm as-is: only the high-confidence, unambiguous Onion line
        # should actually get written.
        confirm_resp = self.client.post(reverse("recipe-import-confirm", args=[job_id]))
        self.assertEqual(confirm_resp.status_code, 200)
        self.assertEqual(confirm_resp.json()["count"], 1)

        self.assertTrue(Recipe.objects.filter(menu_item=self.menu_item, inventory_item=self.onion).exists())
        self.assertFalse(Recipe.objects.filter(menu_item=self.menu_item, inventory_item=self.salt).exists())
        self.assertEqual(Recipe.objects.filter(menu_item=self.menu_item).count(), 1)

        job.refresh_from_db()
        self.assertEqual(job.status, "confirmed")
        self.assertIsNotNone(job.confirmed_at)

    @patch("inventory.tasks.ai_import_recipe.delay")
    @patch("core.ai_service.AIService.match_ingredients")
    @patch("core.ai_service.AIService.parse_recipe")
    def test_discard_leaves_zero_recipe_rows(self, mock_parse_recipe, mock_match_ingredients, mock_delay):
        mock_delay.side_effect = _run_task_synchronously
        mock_parse_recipe.return_value = [{"ingredient": "Onion", "quantity_text": "200g"}]
        mock_match_ingredients.return_value = {}

        job_id = self._start_job().json()["job_id"]
        resp = self.client.post(reverse("recipe-import-discard", args=[job_id]))
        self.assertEqual(resp.status_code, 200)

        self.assertEqual(Recipe.objects.filter(menu_item=self.menu_item).count(), 0)
        job = RecipeImportJob.objects.get(id=job_id)
        self.assertEqual(job.status, "discarded")

    @patch("inventory.tasks.ai_import_recipe.delay")
    @patch("core.ai_service.AIService.match_ingredients")
    @patch("core.ai_service.AIService.parse_recipe")
    def test_confirm_rolls_back_atomically_on_incompatible_unit(
        self, mock_parse_recipe, mock_match_ingredients, mock_delay
    ):
        """The single most important test here: one bad line must not leave
        a half-saved recipe. Onion (valid) and Milk (deliberately given an
        incompatible unit) are confirmed together — if either line fails,
        NEITHER should be written."""
        mock_delay.side_effect = _run_task_synchronously
        mock_parse_recipe.return_value = [
            {"ingredient": "Onion", "quantity_text": "200g"},
            {"ingredient": "Milk", "quantity_text": "100ml"},
        ]
        mock_match_ingredients.return_value = {}

        job_id = self._start_job().json()["job_id"]
        job = RecipeImportJob.objects.get(id=job_id)

        milk_line = job.lines.get(raw_ingredient_name="Milk")
        self.assertTrue(milk_line.include_in_recipe)  # exact match, clean quantity — pre-filled
        # Simulate a reviewer mistake: force the unit to something
        # incompatible with Milk's tracked unit (ml, a volume) instead of
        # going through recipe_import_update_line.
        milk_line.final_unit = "g"
        milk_line.save(update_fields=["final_unit"])

        resp = self.client.post(reverse("recipe-import-confirm", args=[job_id]))
        self.assertEqual(resp.status_code, 400)

        self.assertEqual(Recipe.objects.filter(menu_item=self.menu_item).count(), 0)
        job.refresh_from_db()
        self.assertEqual(job.status, "ready_for_review")  # unchanged — never got to "confirmed"

    @patch("inventory.tasks.ai_import_recipe.delay")
    @patch("core.ai_service.AIService.match_ingredients")
    @patch("core.ai_service.AIService.parse_recipe")
    def test_double_confirm_is_rejected(self, mock_parse_recipe, mock_match_ingredients, mock_delay):
        mock_delay.side_effect = _run_task_synchronously
        mock_parse_recipe.return_value = [{"ingredient": "Onion", "quantity_text": "200g"}]
        mock_match_ingredients.return_value = {}

        job_id = self._start_job().json()["job_id"]
        first = self.client.post(reverse("recipe-import-confirm", args=[job_id]))
        self.assertEqual(first.status_code, 200)

        second = self.client.post(reverse("recipe-import-confirm", args=[job_id]))
        self.assertEqual(second.status_code, 400)
        # Still exactly one Recipe row — the second confirm must not double-write.
        self.assertEqual(Recipe.objects.filter(menu_item=self.menu_item, inventory_item=self.onion).count(), 1)

    def test_feature_flag_gate_rejects_tenant_without_override(self):
        other_tenant = Tenant.objects.create(name="No Feature Tenant", tenant_type="fine_dining")
        other_outlet = Outlet.objects.create(tenant=other_tenant, name="Main")
        User = get_user_model()
        other_owner = User.objects.create_user(
            username="no_feature_owner", password="pwd",
            role="owner", tenant=other_tenant, outlet=other_outlet,
        )
        cat = MenuCategory.objects.create(tenant=other_tenant, outlet=other_outlet, name="Mains")
        other_item = MenuItem.objects.create(
            tenant=other_tenant, outlet=other_outlet, category=cat, name="Dish", price=Decimal("100"),
        )

        client = Client()
        client.force_login(other_owner)
        resp = client.post(reverse("recipe-import-start"), data={"menu_item": other_item.id, "text": "x"})
        self.assertEqual(resp.status_code, 403)

    @patch("inventory.tasks.ai_import_recipe.delay")
    @patch("core.ai_service.AIService.match_ingredients")
    @patch("core.ai_service.AIService.parse_recipe")
    def test_cross_tenant_line_access_is_rejected(self, mock_parse_recipe, mock_match_ingredients, mock_delay):
        mock_delay.side_effect = _run_task_synchronously
        mock_parse_recipe.return_value = [{"ingredient": "Onion", "quantity_text": "200g"}]
        mock_match_ingredients.return_value = {}

        job_id = self._start_job().json()["job_id"]
        job = RecipeImportJob.objects.get(id=job_id)
        line = job.lines.first()

        other_tenant = Tenant.objects.create(name="Attacker Tenant", tenant_type="fine_dining")
        other_outlet = Outlet.objects.create(tenant=other_tenant, name="Main")
        TenantFeatureOverride.objects.create(tenant=other_tenant, feature="ai_recipe_import", enabled=True)
        User = get_user_model()
        attacker = User.objects.create_user(
            username="attacker_owner", password="pwd",
            role="owner", tenant=other_tenant, outlet=other_outlet,
        )
        attacker_client = Client()
        attacker_client.force_login(attacker)

        line_resp = attacker_client.post(
            reverse("recipe-import-update-line", args=[line.id]),
            data="{}", content_type="application/json",
        )
        self.assertEqual(line_resp.status_code, 404)

        job_resp = attacker_client.post(reverse("recipe-import-confirm", args=[job_id]))
        self.assertEqual(job_resp.status_code, 404)

        discard_resp = attacker_client.post(reverse("recipe-import-discard", args=[job_id]))
        self.assertEqual(discard_resp.status_code, 404)
