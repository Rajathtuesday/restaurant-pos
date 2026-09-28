"""
Liquor at an outlet: whether it may sell liquor, and the state VAT rate its
liquor classes start at. The settings screens, the onboarding wizard and the
first pubs' hand setup all go through here, so the rules live in one place.

Liquor is outside GST everywhere; each state decides what it charges at the
bar. Where we have checked a state's rule it is written down here, with the
date it was checked; everywhere else the owner enters the rate.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError

from menu.models import COMPOSITION_SELLS_NO_LIQUOR, MenuItem, VatClass

# The state's VAT on liquor served at a bar, by GST state code (the first two
# digits of a GSTIN), only for states we have checked.
#   29 Karnataka: none. KVAT charged 5.5% at bars, clubs and hotels from
#      1 March 2014; it was withdrawn in the 2017-18 budget and stopped when
#      GST began on 1 July 2017. The state now takes its share as excise,
#      charged by alcohol content since 11 May 2026. Checked 28 September
#      2026 (md_files/gst_vat_karnataka_pub_today_eli5_2026-09-28.html).
DEFAULT_LIQUOR_VAT_RATES = {
    "29": Decimal("0.00"),
}


def state_code(outlet):
    """The outlet's GST state code, from its GSTIN; "" without one."""
    gstin = (outlet.gst_no or "").strip()
    return gstin[:2] if len(gstin) >= 2 and gstin[:2].isdigit() else ""


def default_liquor_vat_rate(outlet):
    """The VAT rate a new liquor class at this outlet starts at, or None
    where the owner has to say (a state we haven't checked, or no GSTIN)."""
    return DEFAULT_LIQUOR_VAT_RATES.get(state_code(outlet))


def composition_refusal(outlet):
    """Why this outlet can't be put on the composition scheme, or None.
    It can't while any of its dishes has a liquor class (D7)."""
    drinks = MenuItem.objects.filter(outlet=outlet, vat_class__isnull=False).count()
    if not drinks:
        return None
    return (
        f"This outlet sells liquor ({drinks} {'drink has' if drinks == 1 else 'drinks have'} a liquor class), "
        "so it can't use the composition scheme: the law bars composition for anyone selling "
        "something outside GST."
    )


def add_liquor_class(outlet, name, rate=None):
    """Create a liquor VAT class at this outlet and return it. Without a
    rate it starts at the state's rate where we know it (Karnataka: 0%);
    elsewhere a rate must be given. Refused for an outlet on composition."""
    if outlet.is_composition_scheme:
        raise ValidationError(COMPOSITION_SELLS_NO_LIQUOR)
    if rate is None:
        rate = default_liquor_vat_rate(outlet)
        if rate is None:
            raise ValidationError("Enter the state's VAT rate on liquor for this outlet.")
    vat_class = VatClass(tenant=outlet.tenant, outlet=outlet, name=(name or "").strip(), rate=Decimal(rate))
    vat_class.full_clean()
    vat_class.save()
    return vat_class
