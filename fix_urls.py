import os
import re

search_dir = "c:/Users/govar/OneDrive/Desktop/pos/restaurant-pos"

replacements = [
    (r"fetch\(`/shifts/([a-zA-Z0-9_-]+)/tips/`", r"fetch(\"{% url 'update_tips' 0 %}\".replace('0', \1)"),
    (r"fetch\('/shifts/clock-in/'", r"fetch(\"{% url 'clock_in' %}\""),
    (r"fetch\('/shifts/clock-out/'", r"fetch(\"{% url 'clock_out' %}\""),
    (r"fetch\(`/running-order-items/\?table=\$\{([a-zA-Z0-9_.]+)\}`\)", r"fetch(\"{% url 'running-order-items' %}?table=\" + \1)"),
    (r"fetch\(`/send-to-kitchen/\$\{([^\}]+)\}/`", r"fetch(\"{% url 'send-to-kitchen' 0 %}\".replace('0', \1)"),
    (r"fetch\(`/cancel-order/\$\{([^\}]+)\}/`", r"fetch(\"{% url 'cancel-order' 0 %}\".replace('0', \1)"),
    (r"fetch\(`/generate-bill/\$\{([^\}]+)\}/`", r"fetch(\"{% url 'generate-bill' 0 %}\".replace('0', \1)"),
    (r"fetch\(`/clean-table/\$\{([^\}]+)\}/`", r"fetch(\"{% url 'clean-table' 0 %}\".replace('0', \1)"),
    (r"fetch\(`/unmerge-tables/\$\{([^\}]+)\}/`", r"fetch(\"{% url 'unmerge-tables' 0 %}\".replace('0', \1)"),
    (r"fetch\(`/resolve-waiter/\$\{([^\}]+)\}/`", r"fetch(\"{% url 'resolve-waiter' 0 %}\".replace('0', \1)"),
    (r"fetch\(`/resolve-kitchen-message/\$\{([^\}]+)\}/`", r"fetch(\"{% url 'resolve-kitchen-message' 0 %}\".replace('0', \1)"),
    (r"fetch\(`/cancel-item/\$\{([^\}]+)\}/`", r"fetch(\"{% url 'cancel-item' 0 %}\".replace('0', \1)"),
    (r"fetch\(`/serve-item/\$\{([^\}]+)\}/`", r"fetch(\"{% url 'serve-item' 0 %}\".replace('0', \1)"),
    (r"fetch\(`/complimentary-item/\$\{([^\}]+)\}/`", r"fetch(\"{% url 'make-complimentary' 0 %}\".replace('0', \1)"),
    (r"fetch\(`/pay/\{\{order\.id\}\}/`", r"fetch(\"{% url 'pay-order' order.id %}\""),
    (r"fetch\(`/crm/lookup/\?phone=\$\{encodeURIComponent\(([^\)]+)\)\}`\)", r"fetch(\"{% url 'crm_lookup' %}?phone=\" + encodeURIComponent(\1))"),
    (r"fetch\(`/crm/link/\$\{([^\}]+)\}/`", r"fetch(\"{% url 'crm_link_order' 0 %}\".replace('0', \1)"),
    (r"fetch\(`/inventory/restock/\$\{([^\}]+)\}/`", r"fetch(\"{% url 'restock_item' 0 %}\".replace('0', \1)"),
    (r"fetch\(`/menu/update-station/\$\{([^\}]+)\}//`", r"fetch(\"{% url 'update_station' 0 %}\".replace('0', \1)"),
    (r"fetch\(`/menu/delete-item/\$\{([^\}]+)\}/`", r"fetch(\"{% url 'delete_menu_item' 0 %}\".replace('0', \1)"),
    (r"fetch\(`/menu/delete-category/\$\{([^\}]+)\}/`", r"fetch(\"{% url 'delete_category' 0 %}\".replace('0', \1)"),
    (r"fetch\(`/menu/toggle-item/\$\{([^\}]+)\}/`", r"fetch(\"{% url 'toggle_item' 0 %}\".replace('0', \1)"),
    (r"fetch\(`/menu/update-price/\$\{([^\}]+)\}/`", r"fetch(\"{% url 'update_price' 0 %}\".replace('0', \1)"),
]

for root, _, files in os.walk(search_dir):
    for fn in files:
        if fn.endswith('.html') or fn.endswith('.js'):
            path = os.path.join(root, fn)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            new_content = content
            for p, r_str in replacements:
                new_content = re.sub(p, r_str, new_content)
            if new_content != content:
                print(f"Updated {path}")
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(new_content)

print("Done URLs")
