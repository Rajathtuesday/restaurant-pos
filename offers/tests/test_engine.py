"""
The offer engine on its own (offers/engine.py): no database, every rule.

Run: python manage.py test offers.tests.test_engine
"""
import random
from datetime import date, datetime, time
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase
from hypothesis import given, settings
from hypothesis import strategies as st

from offers.engine import (
    AMOUNT_OFF, BUY_GET_FREE, PERCENT_OFF, CartLine, Rule, Window, evaluate, live_at,
)

IST = ZoneInfo("Asia/Kolkata")
FRI, SAT = 4, 5

PITCHER, LAGER, NACHOS = 1, 2, 3          # dish ids
BAR, FOOD = 10, 20                         # category ids


def at(day, hour, minute=0):
    """A moment in October 2026 (2 Oct is a Friday)."""
    return datetime(2026, 10, day, hour, minute, tzinfo=IST)


def line(key, price, qty=1, dish=PITCHER, category=BAR, added=None, eligible=True):
    return CartLine(key=key, position=key, dish_id=dish, category_id=category, unit_price=D(price),
                    quantity=qty, added_at=added or at(2, 21), eligible=eligible)


def buy_get(buy=2, free=1, **kw):
    return Rule(id=kw.pop("id", 1), name=kw.pop("name", f"Buy {buy} get {free}"), kind=BUY_GET_FREE,
                buy=buy, free=free, **kw)


def discounts(result):
    return {key: applied.discount for key, applied in result.items()}


class BuyGetFreeTest(SimpleTestCase):
    def test_the_worked_example_the_cheapest_of_three_is_free(self):
        # The design page's bill: 2,300 + 2,500 + 2,300 under "buy 2, get 1".
        lines = [line(1, "2300"), line(2, "2500"), line(3, "2300")]
        result = evaluate(lines, [buy_get(dishes=frozenset({PITCHER}))])
        self.assertEqual(discounts(result), {3: D("2300.00")})
        self.assertEqual(result[3].free_units, 1)
        self.assertEqual(result[3].offer_name, "Buy 2 get 1")

    def test_three_on_one_line_is_one_row_with_one_free(self):
        result = evaluate([line(1, "2300", qty=3)], [buy_get()])
        self.assertEqual(discounts(result), {1: D("2300.00")})
        self.assertEqual(result[1].free_units, 1)

    def test_a_group_that_is_not_full_gets_nothing(self):
        self.assertEqual(evaluate([line(1, "2300", qty=2)], [buy_get()]), {})
        # The fourth pitcher waits for its own group.
        result = evaluate([line(1, "2300", qty=4)], [buy_get()])
        self.assertEqual(discounts(result), {1: D("2300.00")})
        self.assertEqual(discounts(evaluate([line(1, "2300", qty=6)], [buy_get()])), {1: D("4600.00")})

    def test_dearest_first_the_cheapest_of_each_group_is_free(self):
        # 1 + 1 over four different prices: groups (500, 400) and (300, 200).
        lines = [line(1, "300"), line(2, "500"), line(3, "200"), line(4, "400")]
        result = evaluate(lines, [buy_get(buy=1)])
        self.assertEqual(discounts(result), {4: D("400.00"), 3: D("200.00")})

    def test_equal_prices_the_later_line_is_the_free_one(self):
        result = evaluate([line(1, "300"), line(2, "300")], [buy_get(buy=1)])
        self.assertEqual(discounts(result), {2: D("300.00")})

    def test_the_order_lines_come_in_never_matters(self):
        lines = [line(k, p) for k, p in enumerate(["900", "450", "450", "1200", "300", "300", "800"], 1)]
        expected = evaluate(lines, [buy_get(buy=2)])
        for seed in range(20):
            shuffled = lines[:]
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(evaluate(shuffled, [buy_get(buy=2)]), expected)

    def test_buy_two_get_two(self):
        result = evaluate([line(1, "100", qty=4), line(2, "50", qty=4)], [buy_get(buy=2, free=2)])
        # Groups: (100, 100, 100, 100) and (50, 50, 50, 50): two free in each.
        self.assertEqual(discounts(result), {1: D("200.00"), 2: D("100.00")})

    def test_a_rule_without_a_buy_or_free_count_does_nothing(self):
        self.assertEqual(evaluate([line(1, "100", qty=5)], [buy_get(buy=0)]), {})
        self.assertEqual(evaluate([line(1, "100", qty=5)], [buy_get(free=0)]), {})


class WhatCanTakeAnOfferTest(SimpleTestCase):
    def test_free_or_discounted_lines_neither_count_nor_get_it(self):
        # The caller marks a free dish or one with its own discount ineligible:
        # it doesn't complete a group, and it isn't made free.
        lines = [line(1, "2300"), line(2, "2300", eligible=False), line(3, "2300")]
        self.assertEqual(evaluate(lines, [buy_get()]), {})

    def test_only_the_dishes_and_categories_it_covers(self):
        lines = [line(1, "2300"), line(2, "2300"), line(3, "300", dish=NACHOS, category=FOOD),
                 line(4, "400", dish=LAGER)]
        by_dish = buy_get(dishes=frozenset({PITCHER}))
        self.assertEqual(evaluate(lines, [by_dish]), {})
        by_category = buy_get(categories=frozenset({BAR}))
        self.assertEqual(discounts(evaluate(lines, [by_category])), {4: D("400.00")})
        # The whole menu, buy 3 get 1: 2,300 + 2,300 + 400 + 300, the nachos free.
        whole_menu = buy_get(buy=3)
        self.assertEqual(discounts(evaluate(lines, [whole_menu])), {3: D("300.00")})

    def test_a_free_unit_is_the_dish_price_never_its_modifiers(self):
        # unit_price is the dish's own price; the caller never puts modifiers in it.
        result = evaluate([line(1, "250", qty=2)], [buy_get(buy=1)])
        self.assertEqual(discounts(result), {1: D("250.00")})

    def test_zero_price_and_zero_quantity_lines_are_ignored(self):
        self.assertEqual(evaluate([line(1, "0", qty=2), line(2, "100", qty=0)], [buy_get(buy=1)]), {})


class WhenAnOfferCountsTest(SimpleTestCase):
    happy_hour = Rule(id=7, name="Happy hour 25%", kind=PERCENT_OFF, percent=D("25"),
                      windows=(Window(days=frozenset({0, 1, 2, 3, 4}), start=time(17), end=time(20)),))

    def test_the_price_is_locked_when_the_line_is_added(self):
        lines = [line(1, "400", added=at(2, 19, 55)), line(2, "400", added=at(2, 20, 5))]
        self.assertEqual(discounts(evaluate(lines, [self.happy_hour])), {1: D("100.00")})

    def test_a_window_past_midnight_belongs_to_the_night_before(self):
        late = Rule(id=8, name="Friday late", kind=PERCENT_OFF, percent=D("10"),
                    windows=(Window(days=frozenset({FRI}), start=time(20), end=time(2)),))
        self.assertTrue(live_at(late, at(2, 21), 6))           # Friday 9 PM
        self.assertTrue(live_at(late, at(3, 1), 6))            # Saturday 1 AM: Friday night
        self.assertFalse(live_at(late, at(3, 3), 6))           # Saturday 3 AM: after 2
        self.assertFalse(live_at(late, at(2, 1), 6))           # Friday 1 AM: Thursday night
        self.assertFalse(live_at(late, at(3, 21), 6))          # Saturday 9 PM

    def test_a_window_starting_before_six_is_plain_clock_time(self):
        # Found live on 4 Oct 2026: "happy 60", every day 05:00 to 20:00, never
        # showed on a 10:22 bill; the times were counted from the 6 AM start of
        # the business day, so the window was 5 to 6 AM only.
        all_day = Rule(id=14, name="happy 60", kind=PERCENT_OFF, percent=D("60"),
                       windows=(Window(start=time(5), end=time(20)),))
        self.assertTrue(live_at(all_day, at(4, 10, 22), 6))
        self.assertTrue(live_at(all_day, at(4, 5, 30), 6))
        self.assertTrue(live_at(all_day, at(4, 19, 59), 6))
        self.assertFalse(live_at(all_day, at(4, 20, 0), 6))
        self.assertFalse(live_at(all_day, at(4, 4, 59), 6))
        # Its day is the day on the calendar: 4 Oct 2026 is a Sunday.
        sundays = Rule(id=15, name="Sundays", kind=PERCENT_OFF, percent=D("10"),
                       windows=(Window(days=frozenset({6}), start=time(5), end=time(20)),))
        self.assertTrue(live_at(sundays, at(4, 5, 30), 6))
        self.assertFalse(live_at(sundays, at(3, 10), 6))

    def test_dates_are_business_dates(self):
        weekend = Rule(id=9, name="Till Friday", kind=PERCENT_OFF, percent=D("10"),
                       valid_from=date(2026, 10, 2), valid_until=date(2026, 10, 2))
        self.assertTrue(live_at(weekend, at(3, 1), 6))         # Saturday 1 AM is still the 2nd
        self.assertFalse(live_at(weekend, at(3, 7), 6))
        self.assertFalse(live_at(weekend, at(2, 5), 6))        # Friday 5 AM is the 1st

    def test_a_window_with_no_times_is_the_whole_business_day(self):
        friday = Rule(id=10, name="Fridays", kind=PERCENT_OFF, percent=D("10"),
                      windows=(Window(days=frozenset({FRI})),))
        self.assertTrue(live_at(friday, at(2, 6), 6))
        self.assertTrue(live_at(friday, at(3, 5, 59), 6))
        self.assertFalse(live_at(friday, at(3, 6), 6))

    def test_open_ended_windows(self):
        from_eight = Rule(id=11, name="From 8", kind=PERCENT_OFF, percent=D("10"),
                          windows=(Window(start=time(20)),))
        self.assertTrue(live_at(from_eight, at(3, 2), 6))      # to the end of the business day
        self.assertFalse(live_at(from_eight, at(2, 19), 6))
        until_seven = Rule(id=12, name="Till 7", kind=PERCENT_OFF, percent=D("10"),
                           windows=(Window(end=time(19)),))
        self.assertTrue(live_at(until_seven, at(2, 7), 6))
        self.assertFalse(live_at(until_seven, at(2, 19), 6))

    def test_a_paused_offer_keeps_the_lines_added_before(self):
        paused = buy_get(live_until=at(2, 21, 30))
        lines = [line(1, "300", qty=2, added=at(2, 21)), line(2, "300", qty=2, added=at(2, 22))]
        # Only the first line's two units count: one group of... buy 2 needs 3.
        self.assertEqual(evaluate(lines, [paused]), {})
        self.assertEqual(discounts(evaluate(lines, [buy_get(buy=1, live_until=at(2, 21, 30))])),
                         {1: D("300.00")})

    def test_a_line_with_no_time_only_takes_an_offer_without_times(self):
        undated = CartLine(key=1, position=1, dish_id=PITCHER, category_id=BAR, unit_price=D("400"),
                           quantity=1, added_at=None)
        self.assertEqual(evaluate([undated], [self.happy_hour]), {})
        always = Rule(id=13, name="Always", kind=PERCENT_OFF, percent=D("10"))
        self.assertEqual(discounts(evaluate([undated], [always])), {1: D("40.00")})


class OtherKindsTest(SimpleTestCase):
    def test_percent_off_rounds_once_per_line(self):
        rule = Rule(id=1, name="15%", kind=PERCENT_OFF, percent=D("15"))
        # 3 x 333.33 x 15% = 149.9985 -> 150.00
        self.assertEqual(discounts(evaluate([line(1, "333.33", qty=3)], [rule])), {1: D("150.00")})

    def test_amount_off_never_more_than_the_dish(self):
        rule = Rule(id=1, name="Rs 100 off", kind=AMOUNT_OFF, amount=D("100"))
        result = evaluate([line(1, "250", qty=2), line(2, "60")], [rule])
        self.assertEqual(discounts(result), {1: D("200.00"), 2: D("60.00")})

    def test_a_hundred_percent_is_the_most(self):
        rule = Rule(id=1, name="Free", kind=PERCENT_OFF, percent=D("150"))
        self.assertEqual(discounts(evaluate([line(1, "80", qty=2)], [rule])), {1: D("160.00")})


class TwoOffersTest(SimpleTestCase):
    def test_one_offer_per_line_the_higher_priority_first(self):
        lines = [line(1, "300", qty=2)]
        bogo = buy_get(buy=1, id=1, priority=0)
        tenpct = Rule(id=2, name="10%", kind=PERCENT_OFF, percent=D("10"), priority=5)
        result = evaluate(lines, [bogo, tenpct])
        self.assertEqual((result[1].offer_id, result[1].discount), (2, D("60.00")))

    def test_same_priority_the_one_worth_more_to_the_guest(self):
        lines = [line(1, "300", qty=2)]
        bogo = buy_get(buy=1, id=2)
        tenpct = Rule(id=1, name="10%", kind=PERCENT_OFF, percent=D("10"))
        result = evaluate(lines, [tenpct, bogo])
        self.assertEqual((result[1].offer_id, result[1].discount), (2, D("300.00")))

    def test_units_left_out_of_a_group_stay_open_to_another_offer(self):
        # Buy 2 get 1 takes three pitchers; a fourth (its own line) can still
        # get the 10% that covers the whole bar.
        lines = [line(1, "500"), line(2, "500"), line(3, "500"), line(4, "500")]
        bogo = buy_get(id=1, priority=5)
        tenpct = Rule(id=2, name="Bar 10%", kind=PERCENT_OFF, percent=D("10"), categories=frozenset({BAR}))
        result = evaluate(lines, [bogo, tenpct])
        self.assertEqual({k: (a.offer_id, a.discount) for k, a in result.items()},
                         {3: (1, D("500.00")), 4: (2, D("50.00"))})

    def test_the_lines_of_a_full_group_are_closed_even_when_they_pay(self):
        lines = [line(1, "500"), line(2, "500"), line(3, "500")]
        bogo = buy_get(id=1, priority=5)
        tenpct = Rule(id=2, name="Bar 10%", kind=PERCENT_OFF, percent=D("10"))
        result = evaluate(lines, [bogo, tenpct])
        self.assertEqual(set(result), {3})                     # 1 and 2 paid in the group: no 10%


# ------------------------------------------------------------- properties
prices = st.sampled_from(["120", "199.50", "250", "300", "450", "999", "2300", "2500"])
cart = st.lists(st.tuples(prices, st.integers(1, 4), st.sampled_from([PITCHER, LAGER, NACHOS]),
                          st.booleans()), min_size=1, max_size=8)


def _lines(rows):
    return [line(k, p, qty=q, dish=d, category=FOOD if d == NACHOS else BAR, eligible=e)
            for k, (p, q, d, e) in enumerate(rows, 1)]


class PropertiesTest(SimpleTestCase):
    @settings(max_examples=300, deadline=None)
    @given(cart, st.integers(1, 3), st.integers(1, 2))
    def test_buy_get_free_by_the_rule(self, rows, buy, free):
        lines = _lines(rows)
        result = evaluate(lines, [buy_get(buy=buy, free=free)])
        # Reference: every eligible unit, dearest first; in each full group of
        # buy + free, the last `free` are free.
        units = sorted(((D(l.unit_price), l.position, i) for l in lines if l.eligible
                        for i in range(l.quantity)), key=lambda u: (-u[0], u[1], u[2]))
        size = buy + free
        expected = {}
        for start in range(0, len(units) - size + 1, size):
            for price, position, _ in units[start + buy:start + size]:
                expected[position] = expected.get(position, D("0")) + price
        self.assertEqual(discounts(result), {k: v.quantize(D("0.01")) for k, v in expected.items()})

    @settings(max_examples=300, deadline=None)
    @given(cart, st.integers(1, 3), st.sampled_from(["5", "12.5", "33.33", "100"]), st.integers(0, 200))
    def test_bounds_and_order(self, rows, buy, pct, seed):
        lines = _lines(rows)
        rules = [buy_get(buy=buy, id=1, priority=seed % 3),
                 Rule(id=2, name="pct", kind=PERCENT_OFF, percent=D(pct), dishes=frozenset({LAGER, NACHOS})),
                 Rule(id=3, name="off", kind=AMOUNT_OFF, amount=D("75"), categories=frozenset({BAR}))]
        result = evaluate(lines, rules)
        for key, applied in result.items():
            l = next(x for x in lines if x.key == key)
            self.assertTrue(l.eligible)
            self.assertGreater(applied.discount, 0)
            self.assertLessEqual(applied.discount, D(l.unit_price) * l.quantity)
            self.assertEqual(applied.discount, applied.discount.quantize(D("0.01")))
        shuffled = lines[:]
        random.Random(seed).shuffle(shuffled)
        self.assertEqual(evaluate(shuffled, list(reversed(rules))), result)
