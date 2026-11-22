"""
A menu as a file (22 Nov 2026): save an outlet's menu, load it into another.

Why: a Rasova box has no AI menu import (the AI lives on the internet, and a
box has none). So the AI is used where it can be: Rasova reads the
restaurant's menu photo in the cloud, saves the menu as a file, and the file
is loaded on the box. The same file copies a menu to a second outlet, or
keeps a copy before a big change.

The file is JSON, "format": "rasova-menu", "version": 1:

    stations         [{name, made_on_send}]
    vat_classes      [{name, rate}]                     (liquor taxed by VAT)
    modifier_groups  [{name, is_required, max_select, display_order, is_active,
                       modifiers: [{name, price, display_order, is_active}]}]
    categories       [{name, display_order, is_active,
                       items: [{name, description, price, gst_percentage,
                                vat_class, station, estimated_prep_time,
                                display_order, is_available, available_takeaway,
                                available_zomato, available_swiggy, is_veg,
                                parcel_charge, modifier_groups: [names]}]}]

Not in the file: photos (large; the box keeps its own), recipes and stock
links (they point at an outlet's own stock items), prices history.

Loading merges by name and never deletes: a category, a station, a VAT class
or a modifier group with the same name is the same one; a dish with the same
name in the same category is the same dish, updated from the file; a
modifier with the same name in its group likewise. Everything is checked
first, then applied in one transaction, so a bad file changes nothing.
"""
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

FORMAT = "rasova-menu"
VERSION = 1
MAX_BYTES = 2 * 1024 * 1024          # a big menu is ~200 KB; anything near this isn't a menu
MAX_ITEMS = 5000


class MenuFileError(ValueError):
    """A sentence for the owner: what is wrong with the file."""


# ── Saving ──────────────────────────────────────────────────────────────────

def export_menu(outlet):
    """The outlet's whole menu as a dict ready for json.dumps."""
    from menu.models import MenuCategory, MenuItem, MenuItemModifierGroup, ModifierGroup, VatClass
    from setup.models import KitchenStation

    tenant_id = outlet.tenant_id
    stations = KitchenStation.objects.filter(tenant_id=tenant_id, outlet=outlet).order_by("-is_default", "name")
    vat = VatClass.objects.filter(tenant_id=tenant_id, outlet=outlet).order_by("name")
    groups = (ModifierGroup.objects.filter(tenant_id=tenant_id, outlet=outlet)
              .prefetch_related("modifiers").order_by("display_order", "name"))
    links = {}
    for menu_item_id, group_name in (MenuItemModifierGroup.objects
                                     .filter(menu_item__tenant_id=tenant_id, menu_item__outlet=outlet)
                                     .values_list("menu_item_id", "modifier_group__name")):
        links.setdefault(menu_item_id, []).append(group_name)
    items_by_category = {}
    for item in (MenuItem.objects.filter(tenant_id=tenant_id, outlet=outlet)
                 .select_related("station", "vat_class").order_by("display_order", "name", "id")):
        items_by_category.setdefault(item.category_id, []).append({
            "name": item.name,
            "description": item.description,
            "price": str(item.price),
            "gst_percentage": str(item.gst_percentage),
            "vat_class": item.vat_class.name if item.vat_class_id else None,
            "station": item.station.name if item.station_id else None,
            "estimated_prep_time": item.estimated_prep_time,
            "display_order": item.display_order,
            "is_available": item.is_available,
            "available_takeaway": item.available_takeaway,
            "available_zomato": item.available_zomato,
            "available_swiggy": item.available_swiggy,
            "is_veg": item.is_veg,
            "parcel_charge": None if item.parcel_charge is None else str(item.parcel_charge),
            "modifier_groups": sorted(links.get(item.id, [])),
        })
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": timezone.now().isoformat(timespec="seconds"),
        "from": {"restaurant": outlet.tenant.name, "outlet": outlet.name},
        "stations": [{"name": s.name, "made_on_send": s.made_on_send} for s in stations],
        "vat_classes": [{"name": v.name, "rate": str(v.rate)} for v in vat],
        "modifier_groups": [{
            "name": g.name, "is_required": g.is_required, "max_select": g.max_select,
            "display_order": g.display_order, "is_active": g.is_active,
            "modifiers": [{"name": m.name, "price": str(m.price), "display_order": m.display_order,
                           "is_active": m.is_active}
                          for m in sorted(g.modifiers.all(), key=lambda m: (m.display_order, m.name))],
        } for g in groups],
        "categories": [{
            "name": c.name, "display_order": c.display_order, "is_active": c.is_active,
            "items": items_by_category.get(c.id, []),
        } for c in MenuCategory.objects.filter(tenant_id=tenant_id, outlet=outlet).order_by("display_order", "name")],
    }


def export_filename(outlet):
    from django.utils.text import slugify
    return f"rasova-menu-{slugify(outlet.tenant.name) or 'menu'}-{timezone.localdate().isoformat()}.json"


# ── Reading (everything checked before anything changes) ────────────────────

@dataclass
class Plan:
    """What loading a file would do, and the clean data to do it with."""
    data: dict
    new: dict = field(default_factory=lambda: {"categories": 0, "items": 0, "stations": 0,
                                               "vat_classes": 0, "modifier_groups": 0, "modifiers": 0})
    updated: dict = field(default_factory=lambda: {"items": 0, "modifiers": 0})
    source: str = ""

    @property
    def item_count(self):
        return sum(len(c["items"]) for c in self.data["categories"])


def _text(value, what, limit, required=True):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise MenuFileError(f"{what} must be text.")
    value = value.strip()
    if required and not value:
        raise MenuFileError(f"{what} is empty.")
    if len(value) > limit:
        raise MenuFileError(f"{what} is longer than {limit} characters.")
    return value


def _money(value, what, allow_none=False, most=Decimal("99999999.99")):
    """An amount the database column can hold (most = its max_digits limit:
    a parcel charge of 10,000 would otherwise fail on save)."""
    if value is None and allow_none:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise MenuFileError(f"{what} is not an amount.")
    if not amount.is_finite() or amount < 0 or amount > most:
        raise MenuFileError(f"{what} must be an amount from 0 to {most:,.2f}.")
    return amount.quantize(Decimal("0.01"))


def _whole(value, what, low=0, high=100000, default=0):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise MenuFileError(f"{what} must be a whole number from {low} to {high}.")
    return value


def _flag(value, what, default):
    if value is None:
        return default
    if not isinstance(value, bool):
        raise MenuFileError(f"{what} must be true or false.")
    return value


def _list(value, what):
    if value is None:
        return []
    if not isinstance(value, list):
        raise MenuFileError(f"{what} must be a list.")
    return value


def read_menu_file(raw_bytes):
    """Parse and check a menu file. Returns the clean data, or raises
    MenuFileError with a sentence that names what is wrong."""
    from orders.services.tax_service import GST_RATES

    if len(raw_bytes) > MAX_BYTES:
        raise MenuFileError("That file is too big to be a menu file (over 2 MB).")
    try:
        data = json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise MenuFileError("That isn't a Rasova menu file: it can't be read.")
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise MenuFileError("That isn't a Rasova menu file.")
    if data.get("version") != VERSION:
        raise MenuFileError("This menu file was made by a newer Rasova. Update this Rasova first, then load it.")

    clean = {"stations": [], "vat_classes": [], "modifier_groups": [], "categories": []}
    seen = set()
    for s in _list(data.get("stations"), "The stations"):
        if not isinstance(s, dict):
            raise MenuFileError("A station in the file is damaged.")
        name = _text(s.get("name"), "A station's name", 100)
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        clean["stations"].append({"name": name, "made_on_send": _flag(s.get("made_on_send"), f"{name}'s made-on-send", False)})

    seen = set()
    for v in _list(data.get("vat_classes"), "The VAT classes"):
        if not isinstance(v, dict):
            raise MenuFileError("A VAT class in the file is damaged.")
        name = _text(v.get("name"), "A VAT class's name", 40)
        rate = _money(v.get("rate"), f"VAT class {name}'s rate", most=Decimal("100"))
        if name.lower() not in seen:
            seen.add(name.lower())
            clean["vat_classes"].append({"name": name, "rate": rate})
    vat_names = {v["name"].lower() for v in clean["vat_classes"]}

    seen = set()
    for g in _list(data.get("modifier_groups"), "The modifier groups"):
        if not isinstance(g, dict):
            raise MenuFileError("A modifier group in the file is damaged.")
        name = _text(g.get("name"), "A modifier group's name", 100)
        if name.lower() in seen:
            raise MenuFileError(f"The modifier group {name} is in the file twice.")
        seen.add(name.lower())
        mods, mod_seen = [], set()
        for m in _list(g.get("modifiers"), f"{name}'s modifiers"):
            if not isinstance(m, dict):
                raise MenuFileError(f"A modifier in {name} is damaged.")
            mname = _text(m.get("name"), f"A modifier's name in {name}", 100)
            if mname.lower() in mod_seen:
                continue
            mod_seen.add(mname.lower())
            mods.append({"name": mname, "price": _money(m.get("price", "0"), f"{mname}'s price"),
                         "display_order": _whole(m.get("display_order"), f"{mname}'s order"),
                         "is_active": _flag(m.get("is_active"), f"{mname}'s on/off", True)})
        clean["modifier_groups"].append({
            "name": name, "is_required": _flag(g.get("is_required"), f"{name}'s required", False),
            "max_select": _whole(g.get("max_select"), f"{name}'s most choices", 1, 100, 1),
            "display_order": _whole(g.get("display_order"), f"{name}'s order"),
            "is_active": _flag(g.get("is_active"), f"{name}'s on/off", True), "modifiers": mods,
        })
    group_names = {g["name"].lower() for g in clean["modifier_groups"]}
    station_names = {s["name"].lower() for s in clean["stations"]}

    seen, total = set(), 0
    for c in _list(data.get("categories"), "The categories"):
        if not isinstance(c, dict):
            raise MenuFileError("A category in the file is damaged.")
        cname = _text(c.get("name"), "A category's name", 255)
        if cname.lower() in seen:
            raise MenuFileError(f"The category {cname} is in the file twice.")
        seen.add(cname.lower())
        items, item_seen = [], set()
        for it in _list(c.get("items"), f"{cname}'s dishes"):
            if not isinstance(it, dict):
                raise MenuFileError(f"A dish in {cname} is damaged.")
            iname = _text(it.get("name"), f"A dish's name in {cname}", 255)
            if iname.lower() in item_seen:
                raise MenuFileError(f"{iname} is in {cname} twice.")
            item_seen.add(iname.lower())
            gst = _money(it.get("gst_percentage", "5.00"), f"{iname}'s GST")
            if gst not in GST_RATES:
                raise MenuFileError(f"{iname}'s GST is {gst}%: Rasova bills at 0%, 5% or 18%.")
            vat = it.get("vat_class")
            if vat is not None:
                vat = _text(vat, f"{iname}'s VAT class", 40)
                if vat.lower() not in vat_names:
                    raise MenuFileError(f"{iname} is taxed by the VAT class {vat}, which isn't in the file.")
                if gst:
                    raise MenuFileError(f"{iname} is a drink taxed by VAT, so its GST must be 0%.")
            station = it.get("station")
            if station is not None:
                station = _text(station, f"{iname}'s station", 100)
                if station.lower() not in station_names:
                    clean["stations"].append({"name": station, "made_on_send": False})
                    station_names.add(station.lower())
            groups = []
            for gname in _list(it.get("modifier_groups"), f"{iname}'s modifier groups"):
                gname = _text(gname, f"A modifier group of {iname}", 100)
                if gname.lower() not in group_names:
                    raise MenuFileError(f"{iname} uses the modifier group {gname}, which isn't in the file.")
                if gname.lower() not in {g.lower() for g in groups}:
                    groups.append(gname)
            items.append({
                "name": iname,
                "description": _text(it.get("description"), f"{iname}'s description", 2000, required=False),
                "price": _money(it.get("price"), f"{iname}'s price"),
                "gst_percentage": gst, "vat_class": vat, "station": station,
                "estimated_prep_time": _whole(it.get("estimated_prep_time"), f"{iname}'s preparation time", 0, 600, 15),
                "display_order": _whole(it.get("display_order"), f"{iname}'s order"),
                "is_available": _flag(it.get("is_available"), f"{iname}'s availability", True),
                "available_takeaway": _flag(it.get("available_takeaway"), f"{iname}'s takeaway", True),
                "available_zomato": _flag(it.get("available_zomato"), f"{iname}'s Zomato", True),
                "available_swiggy": _flag(it.get("available_swiggy"), f"{iname}'s Swiggy", True),
                "is_veg": _flag(it.get("is_veg"), f"{iname}'s veg", True),
                "parcel_charge": _money(it.get("parcel_charge"), f"{iname}'s parcel charge", allow_none=True,
                                        most=Decimal("9999.99")),
                "modifier_groups": groups,
            })
        total += len(items)
        if total > MAX_ITEMS:
            raise MenuFileError(f"The file has more than {MAX_ITEMS} dishes: that isn't one restaurant's menu.")
        clean["categories"].append({
            "name": cname, "display_order": _whole(c.get("display_order"), f"{cname}'s order"),
            "is_active": _flag(c.get("is_active"), f"{cname}'s on/off", True), "items": items,
        })
    source = data.get("from") if isinstance(data.get("from"), dict) else {}
    clean["source"] = " · ".join(str(source.get(k))[:100] for k in ("restaurant", "outlet") if source.get(k))
    return clean


# ── Loading ─────────────────────────────────────────────────────────────────

def plan_import(outlet, clean):
    """What loading would add and update, without changing anything. Also
    refuses what this outlet can't take (liquor on a composition outlet)."""
    from menu.models import COMPOSITION_SELLS_NO_LIQUOR, MenuCategory, MenuItem, Modifier, ModifierGroup, VatClass
    from setup.models import KitchenStation

    if outlet.is_composition_scheme and any(it["vat_class"] for c in clean["categories"] for it in c["items"]):
        raise MenuFileError(COMPOSITION_SELLS_NO_LIQUOR)
    plan = Plan(data=clean, source=clean.get("source", ""))
    t = outlet.tenant_id
    have_stations = {n.lower() for n in KitchenStation.objects.filter(tenant_id=t, outlet=outlet).values_list("name", flat=True)}
    have_vat = {n.lower() for n in VatClass.objects.filter(tenant_id=t, outlet=outlet).values_list("name", flat=True)}
    have_groups = {g.name.lower(): g for g in ModifierGroup.objects.filter(tenant_id=t, outlet=outlet)}
    have_cats = {n.lower() for n in MenuCategory.objects.filter(tenant_id=t, outlet=outlet).values_list("name", flat=True)}
    have_items = {(c.lower(), n.lower()) for c, n in
                  MenuItem.objects.filter(tenant_id=t, outlet=outlet).values_list("category__name", "name")}
    have_mods = {(g.lower(), n.lower()) for g, n in
                 Modifier.objects.filter(group__tenant_id=t, group__outlet=outlet).values_list("group__name", "name")}
    plan.new["stations"] = sum(1 for s in clean["stations"] if s["name"].lower() not in have_stations)
    plan.new["vat_classes"] = sum(1 for v in clean["vat_classes"] if v["name"].lower() not in have_vat)
    for g in clean["modifier_groups"]:
        if g["name"].lower() not in have_groups:
            plan.new["modifier_groups"] += 1
        for m in g["modifiers"]:
            key = (g["name"].lower(), m["name"].lower())
            if key in have_mods:
                plan.updated["modifiers"] += 1
            else:
                plan.new["modifiers"] += 1
    for c in clean["categories"]:
        if c["name"].lower() not in have_cats:
            plan.new["categories"] += 1
        for it in c["items"]:
            if (c["name"].lower(), it["name"].lower()) in have_items:
                plan.updated["items"] += 1
            else:
                plan.new["items"] += 1
    return plan


@transaction.atomic
def apply_import(outlet, clean):
    """Load the checked file into the outlet. One transaction: all or nothing."""
    from menu.models import MenuCategory, MenuItem, MenuItemModifierGroup, Modifier, ModifierGroup, VatClass
    from setup.models import KitchenStation

    plan = plan_import(outlet, clean)          # the same refusals, inside the transaction
    t = outlet.tenant_id

    stations = {s.name.lower(): s for s in KitchenStation.objects.filter(tenant_id=t, outlet=outlet)}
    for s in clean["stations"]:
        if s["name"].lower() not in stations:
            stations[s["name"].lower()] = KitchenStation.objects.create(
                tenant_id=t, outlet=outlet, name=s["name"], made_on_send=s["made_on_send"],
                is_default=not stations)              # an outlet with no station gets this one as its default

    vat = {v.name.lower(): v for v in VatClass.objects.filter(tenant_id=t, outlet=outlet)}
    for v in clean["vat_classes"]:
        if v["name"].lower() not in vat:
            vat[v["name"].lower()] = VatClass.objects.create(tenant_id=t, outlet=outlet, name=v["name"], rate=v["rate"])

    groups = {g.name.lower(): g for g in ModifierGroup.objects.filter(tenant_id=t, outlet=outlet)}
    for g in clean["modifier_groups"]:
        group = groups.get(g["name"].lower())
        if group is None:
            group = groups[g["name"].lower()] = ModifierGroup.objects.create(
                tenant_id=t, outlet=outlet, name=g["name"], is_required=g["is_required"],
                max_select=g["max_select"], display_order=g["display_order"], is_active=g["is_active"])
        existing = {m.name.lower(): m for m in group.modifiers.all()}
        for m in g["modifiers"]:
            mod = existing.get(m["name"].lower())
            if mod is None:
                Modifier.objects.create(group=group, name=m["name"], price=m["price"],
                                        display_order=m["display_order"], is_active=m["is_active"])
            else:
                mod.price, mod.display_order, mod.is_active = m["price"], m["display_order"], m["is_active"]
                mod.save(update_fields=["price", "display_order", "is_active"])

    categories = {c.name.lower(): c for c in MenuCategory.objects.filter(tenant_id=t, outlet=outlet)}
    fields = ("description", "price", "gst_percentage", "estimated_prep_time", "display_order", "is_available",
              "available_takeaway", "available_zomato", "available_swiggy", "is_veg", "parcel_charge")
    for c in clean["categories"]:
        category = categories.get(c["name"].lower())
        if category is None:
            category = categories[c["name"].lower()] = MenuCategory.objects.create(
                tenant_id=t, outlet=outlet, name=c["name"], display_order=c["display_order"], is_active=c["is_active"])
        existing = {}
        for item in MenuItem.objects.filter(tenant_id=t, outlet=outlet, category=category).order_by("id"):
            existing.setdefault(item.name.lower(), item)
        for it in c["items"]:
            item = existing.get(it["name"].lower())
            values = {f: it[f] for f in fields}
            if values["parcel_charge"] is None:
                values.pop("parcel_charge")             # keep the outlet's own default
            values["vat_class"] = vat[it["vat_class"].lower()] if it["vat_class"] else None
            values["station"] = stations[it["station"].lower()] if it["station"] else None
            if item is None:
                item = MenuItem.objects.create(tenant_id=t, outlet=outlet, category=category, name=it["name"], **values)
            else:
                for name, value in values.items():
                    setattr(item, name, value)
                item.save(update_fields=list(values))
            have = set(MenuItemModifierGroup.objects.filter(menu_item=item).values_list("modifier_group_id", flat=True))
            for gname in it["modifier_groups"]:
                group = groups[gname.lower()]
                if group.id not in have:
                    MenuItemModifierGroup.objects.create(menu_item=item, modifier_group=group)
    return plan
