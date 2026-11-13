"""
Taking an order with the network cut, in a real browser.

The promise of offline mode is one sentence: an order taken while the
internet is down reaches the kitchen once it is back, exactly once. The
module tests prove the queue with fakes; this cuts the browser's network,
takes an order on the real billing screen, brings the network back and
counts the orders in the database.

The second test is the case that used to make two orders: the server took
the order, and the connection dropped before its answer reached the
tablet. The tablet could not know, kept the order and sent it again.
Every request now carries its own id (orders.OrderRequest), so the second
arrival is answered with the first order.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test orders.tests.test_offline_order_browser
"""
from django.conf import settings
from django.test import Client

from core.tests.browser import HELPERS, _console_error, _wait_until_the_server_is_idle
from orders.models import Order, OrderRequest
from orders.tests.test_billing_browser import ADD_DISH, BillingBrowserBase

# What the browser says when a request cannot leave or is cut: expected here.
CUT = ("ERR_INTERNET_DISCONNECTED", "ERR_CONNECTION_RESET", "ERR_FAILED", "Failed to fetch")

TOAST = """
const toast = (word) => t.until(() => {
  const el = document.querySelector(".swal2-toast .swal2-title");
  return el && el.textContent.includes(word) && el.textContent;
});
const queued = async () => (await window.offlineQueue.all()).length;
"""


class OfflineOrderTest(BillingBrowserBase):

    def open_billing(self, **context_options):
        """The billing screen as the owner. Returns (context, page, errors);
        the test closes the context."""
        client = Client()
        client.force_login(self.owner)
        context = self.browser.new_context(viewport={"width": 1400, "height": 900}, **context_options)
        self.addCleanup(_wait_until_the_server_is_idle)
        self.addCleanup(context.close)
        context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                              "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                              "url": self.live_server_url}])
        context.add_init_script(HELPERS)
        page = context.new_page()
        page.set_default_timeout(30_000)
        errors = []
        page.on("pageerror", lambda error: errors.append(f"uncaught: {error}"))
        page.on("console", lambda message: errors.append(_console_error(message)) if message.type == "error" else None)
        page.goto(self.live_server_url + "/billing/", wait_until="load")
        return context, page, errors

    def run_js(self, page, script):
        return page.evaluate("async () => {" + script + "}")

    def unexpected(self, errors):
        return [e for e in errors if not any(cut in e for cut in CUT)]

    def orders(self):
        return Order.objects.filter(tenant=self.tenant)

    def test_an_order_taken_with_no_internet_reaches_the_kitchen_once(self):
        context, page, errors = self.open_billing()
        self.run_js(page, ADD_DISH + f"await addDish({self.paneer.id}); await addDish({self.curd.id});")

        context.set_offline(True)
        saved = self.run_js(page, TOAST + """
            await t.until(() => !navigator.onLine);
            t.click('.context-sidebar [data-action="order.submit"]');
            const said = await toast("Saved");
            return { said, queued: await queued(), cart: t.text("#cartItemsContainerDesktop"),
                     banner: t.text("#offline-banner") };
        """)
        self.assertEqual(saved["said"], "Saved. It will be sent to the kitchen once back online.")
        self.assertEqual(saved["queued"], 1)
        self.assertEqual(saved["cart"], "Empty Order")
        self.assertIn("1 item saved", saved["banner"])
        self.assertEqual(self.orders().count(), 0, "an order reached the server with the network cut")

        context.set_offline(False)
        synced = self.run_js(page, TOAST + """
            const said = await toast("synced");
            await t.until(async () => (await queued()) === 0);
            return said;
        """)
        self.assertIn("synced and sent to kitchen", synced)

        order = self.orders().get()
        self.assertEqual(sorted((i.menu_item.name, i.quantity, i.status) for i in order.items.all()),
                         [("Curd", 1, "sent"), ("Paneer Tikka", 1, "sent")])
        self.assertEqual(OrderRequest.objects.for_outlet(self.tenant, self.outlet).count(), 1)
        self.assertEqual(self.unexpected(errors), [])

    def test_an_answer_lost_on_the_way_back_does_not_make_a_second_order(self):
        # The service worker is not the subject here, and a request it has
        # seen cannot be cut by the test.
        context, page, errors = self.open_billing(service_workers="block")
        self.run_js(page, ADD_DISH + f"await addDish({self.paneer.id});")

        cut = []

        def lose_the_answer(route):
            route.fetch()                       # the server takes the order
            cut.append(route.request.url)
            route.abort("connectionreset")      # and the tablet never hears

        page.route("**/create-order/", lose_the_answer)
        dropped = self.run_js(page, TOAST + """
            t.click('.context-sidebar [data-action="order.submit"]');
            const said = await toast("Connection dropped");
            return { said, queued: await queued() };
        """)
        page.unroute("**/create-order/")
        self.assertEqual(len(cut), 1)
        self.assertEqual(dropped["queued"], 1, "the tablet did not keep the order it could not confirm")
        self.assertEqual(self.orders().count(), 1, "the server should already have the order")

        self.run_js(page, TOAST + """
            await window.offlineQueue.flush();
            await t.until(async () => (await queued()) === 0);
        """)
        self.assertEqual(self.orders().count(), 1, "the saved copy was sent again and made a second order")
        order = self.orders().get()
        self.assertEqual([(i.menu_item.name, i.quantity) for i in order.items.all()], [("Paneer Tikka", 1)])
        self.assertEqual(self.unexpected(errors), [])
