# import os
# import django
# import random
# import time
# import threading
# import requests

# # ===============================
# # DJANGO SETUP
# # ===============================

# os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
# django.setup()

# from tenants.models import Tenant, Outlet
# from accounts.models import User
# from orders.models import Table
# from menu.models import MenuCategory, MenuItem


# BASE_URL = "http://127.0.0.1:8000"

# session = requests.Session()


# # ===============================
# # BOOTSTRAP DATABASE
# # ===============================

# def bootstrap_data():

#     print("Bootstrapping restaurant...")

#     tenant, _ = Tenant.objects.get_or_create(
#         name="Test Restaurant"
#     )

#     outlet, _ = Outlet.objects.get_or_create(
#         tenant=tenant,
#         name="Main Branch"
#     )

#     print("Creating staff users...")

#     roles = ["owner","manager","cashier","waiter","chef"]

#     for role in roles:

#         username = f"{role}1"

#         if not User.objects.filter(username=username).exists():

#             User.objects.create_user(
#                 username=username,
#                 password="1234",
#                 role=role,
#                 tenant=tenant,
#                 outlet=outlet
#             )

#     print("Creating tables...")

#     for i in range(1,11):

#         Table.objects.get_or_create(
#             tenant=tenant,
#             outlet=outlet,
#             name=f"Table {i}"
#         )

#     print("Creating menu...")

#     category, _ = MenuCategory.objects.get_or_create(
#         tenant=tenant,
#         outlet=outlet,
#         name="Main Menu"
#     )

#     items = [
#         ("Burger",120),
#         ("Pizza",250),
#         ("Pasta",180),
#         ("Coke",60),
#         ("Coffee",80),
#     ]

#     for name,price in items:

#         MenuItem.objects.get_or_create(
#             tenant=tenant,
#             outlet=outlet,
#             category=category,
#             name=name,
#             price=price
#         )

#     print("Bootstrap complete")


# # ===============================
# # LOGIN
# # ===============================

# def login():

#     print("Logging in...")

#     r = session.get(f"{BASE_URL}/login/")

#     csrftoken = r.cookies.get("csrftoken")

#     payload = {
#         "username": "owner1",
#         "password": "1234",
#         "csrfmiddlewaretoken": csrftoken
#     }

#     session.post(
#         f"{BASE_URL}/login/",
#         data=payload,
#         headers={"Referer": f"{BASE_URL}/login/"}
#     )

#     print("Login success")


# # ===============================
# # ORDER GENERATOR
# # ===============================

# menu_items = list(MenuItem.objects.all())

# tables = list(Table.objects.all())


# def random_cart():

#     cart = []

#     for _ in range(random.randint(1,3)):

#         item = random.choice(menu_items)

#         cart.append({
#             "id": item.id,
#             "quantity": random.randint(1,3)
#         })

#     return cart


# # ===============================
# # POS SIMULATION
# # ===============================

# def create_order(table_id):

#     cart = random_cart()

#     payload = {
#         "cart": cart,
#         "table_id": table_id
#     }

#     r = session.post(
#         f"{BASE_URL}/create-order/",
#         json=payload
#     )

#     if r.status_code != 200:

#         print("Order error:", r.text)
#         return None

#     data = r.json()

#     print(f"Order created for table {table_id}")

#     return data["order_id"]


# def send_kitchen(order_id):

#     session.post(
#         f"{BASE_URL}/send-to-kitchen/{order_id}/"
#     )


# def generate_bill(table_id):

#     r = session.post(
#         f"{BASE_URL}/generate-bill/{table_id}/"
#     )

#     if r.status_code != 200:

#         print("Bill error", r.text)
#         return None

#     data = r.json()

#     return data["order_id"]


# def pay_order(order_id):

#     payload = {
#         "method": random.choice(["cash","upi","card"])
#     }

#     session.post(
#         f"{BASE_URL}/pay/{order_id}/",
#         json=payload
#     )

#     print("Payment completed for order", order_id)


# # ===============================
# # TABLE SIMULATION
# # ===============================

# def simulate_table():

#     table = random.choice(tables)

#     try:

#         order_id = create_order(table.id)

#         if not order_id:
#             return

#         time.sleep(random.uniform(1,2))

#         send_kitchen(order_id)

#         time.sleep(random.uniform(2,4))

#         bill_id = generate_bill(table.id)

#         if bill_id:

#             time.sleep(1)

#             pay_order(bill_id)

#     except Exception as e:

#         print("Simulation error:", e)


# # ===============================
# # LOAD TEST
# # ===============================

# def run_simulation():

#     threads = []

#     for _ in range(30):   # number of simulated orders

#         t = threading.Thread(target=simulate_table)

#         threads.append(t)

#         t.start()

#         time.sleep(random.uniform(0.5,1.5))

#     for t in threads:
#         t.join()

#     print("Simulation finished")


# # ===============================
# # MAIN
# # ===============================

# if __name__ == "__main__":

#     bootstrap_data()

#     login()

#     run_simulation()







import os
import django
import random
import time
import threading
import requests

# ===============================
# DJANGO SETUP
# ===============================

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")   # change if your project name is different
django.setup()

from tenants.models import Tenant, Outlet
from accounts.models import User
from orders.models import Table
from menu.models import MenuCategory, MenuItem


BASE_URL = "http://127.0.0.1:8000"

session = requests.Session()


# ===============================
# BOOTSTRAP DATA
# ===============================

def bootstrap():

    print("\nBootstrapping test data...\n")

    tenant, _ = Tenant.objects.get_or_create(
        name="Test Restaurant"
    )

    outlet, _ = Outlet.objects.get_or_create(
        tenant=tenant,
        name="Main Outlet"
    )

    roles = ["owner","manager","cashier","waiter","chef"]

    for role in roles:

        username = f"{role}1"

        if not User.objects.filter(username=username).exists():

            User.objects.create_user(
                username=username,
                password="1234",
                role=role,
                tenant=tenant,
                outlet=outlet
            )

            print("Created user:", username)

    for i in range(1,11):

        Table.objects.get_or_create(
            tenant=tenant,
            outlet=outlet,
            name=f"Table {i}"
        )

    print("Tables ready")

    category, _ = MenuCategory.objects.get_or_create(
        tenant=tenant,
        outlet=outlet,
        name="Main Menu"
    )

    items = [
        ("Burger",120),
        ("Pizza",250),
        ("Pasta",180),
        ("Coke",60),
        ("Coffee",80)
    ]

    for name,price in items:

        MenuItem.objects.get_or_create(
            tenant=tenant,
            outlet=outlet,
            category=category,
            name=name,
            price=price
        )

    print("Menu ready\n")


# ===============================
# LOGIN
# ===============================

def login():

    print("Logging in...")

    # load login page to get CSRF
    r = session.get(f"{BASE_URL}/login/")

    csrftoken = session.cookies.get("csrftoken")

    payload = {
        "username":"owner1",
        "password":"1234",
        "csrfmiddlewaretoken": csrftoken
    }

    headers = {
        "Referer": f"{BASE_URL}/login/"
    }

    session.post(
        f"{BASE_URL}/login/",
        data=payload,
        headers=headers
    )

    print("Login successful\n")


# ===============================
# POST HELPER WITH CSRF
# ===============================

def post(url,data):

    csrftoken = session.cookies.get("csrftoken")

    headers = {
        "X-CSRFToken": csrftoken,
        "Referer": BASE_URL
    }

    return session.post(url,json=data,headers=headers)


# ===============================
# SIMULATION DATA
# ===============================

tables = list(Table.objects.all())
menu_items = list(MenuItem.objects.all())


def random_cart():

    cart = []

    for _ in range(random.randint(1,3)):

        item = random.choice(menu_items)

        cart.append({
            "id": item.id,
            "quantity": random.randint(1,3)
        })

    return cart


# ===============================
# POS FLOW
# ===============================

def create_order(table):

    payload = {
        "table_id": table.id,
        "cart": random_cart()
    }

    r = post(f"{BASE_URL}/create-order/",payload)

    if r.status_code != 200:

        print("Order error:",r.status_code)
        return None

    data = r.json()

    print(f"Order created for table {table.name}")

    return data["order_id"]


def send_to_kitchen(order_id):

    post(
        f"{BASE_URL}/send-to-kitchen/{order_id}/",
        {}
    )

    print("KOT sent",order_id)


def generate_bill(table):

    r = post(
        f"{BASE_URL}/generate-bill/{table.id}/",
        {}
    )

    if r.status_code != 200:

        print("Bill error",r.status_code)
        return None

    data = r.json()

    print("Bill generated",data["order_id"])

    return data["order_id"]


def pay(order_id):

    payload = {
        "method": random.choice(["cash","upi","card"])
    }

    post(
        f"{BASE_URL}/pay/{order_id}/",
        payload
    )

    print("Payment done",order_id)


# ===============================
# TABLE SIMULATION
# ===============================

def simulate_table():

    table = random.choice(tables)

    try:

        order_id = create_order(table)

        if not order_id:
            return

        time.sleep(random.uniform(1,3))

        send_to_kitchen(order_id)

        time.sleep(random.uniform(2,4))

        bill_id = generate_bill(table)

        if bill_id:

            time.sleep(1)

            pay(bill_id)

    except Exception as e:

        print("Simulation error:",e)


# ===============================
# LOAD TEST
# ===============================

def run_test():

    threads = []

    print("\nStarting POS simulation...\n")

    for _ in range(30):

        t = threading.Thread(target=simulate_table)

        threads.append(t)

        t.start()

        time.sleep(random.uniform(0.3,1))

    for t in threads:
        t.join()

    print("\nSimulation finished\n")


# ===============================
# MAIN
# ===============================

if __name__ == "__main__":

    bootstrap()

    login()

    run_test()