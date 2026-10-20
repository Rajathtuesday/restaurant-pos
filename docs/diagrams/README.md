# Model diagrams

Drawn from the models themselves by `python manage.py make_model_diagrams` (django-extensions' `graph_models`, then Graphviz), so they match the code on the day they were drawn. Last drawn 21 October 2026: 74 Rasova models in 21 apps.

Each picture comes as a `.png` and a `.svg`. The `.svg` stays sharp at any zoom, so open that one in a browser for the big diagrams.

| File | What it shows |
|---|---|
| `pos_core_schema` | The restaurant's core: tenants and outlets, staff, menu, orders and their items, KOTs and the kitchen, payments and refunds, printing, QSR tokens, tables, merges and waiter calls |
| `pos_lr`, `pos_rl` | The same core, drawn left to right and right to left (sometimes easier to follow) |
| `pos_er_diagram` | Every model, Django's own and third-party ones included |
| `pos_grouped` | Every model, boxed by app |
| `apps/<app>` | One per Rasova app: its models with every field, and the models it points at as small labels |
| `schema.dot` | The Graphviz source of the full diagram |

How to read them:

- Each box is a model; **bold** fields are required, grey ones may be empty.
- A line with a dot ends at the model that holds the foreign key; the label says the field and, in brackets, the reverse name.
- `<TenantScopedModel>` under a model's name means it belongs to one restaurant (tenant and outlet). The base class itself is left out of the pictures: nearly every model inherits it, and its arrows hid the real relations.

To redraw after changing models:

```bash
python manage.py make_model_diagrams              # needs Graphviz (dot) installed
python manage.py make_model_diagrams --dot-only   # only the .dot files, without Graphviz
```
