"""Catalog reads for crude-clover.

The orders endpoint omits the category dimension: a line item carries an
``item`` FK into inventory, and the category lives on the inventory item. So a
category-split report needs the catalog dumped once and joined against line
items. ``dump`` pulls items (with their categories expanded), the category list,
and the modifier groups (with their modifiers), as one structure that flatten
reads to resolve ``item.id`` to a category name.
"""

from __future__ import annotations


class CatalogAPI:
    def __init__(self, session):
        self.session = session

    def dump(self) -> dict:
        """Items (categories expanded), categories, and modifier groups."""
        base = f"/v3/merchants/{self.session.merchant_id}"
        return {
            "items": list(self.session.iter_elements(f"{base}/items", expand="categories")),
            "categories": list(self.session.iter_elements(f"{base}/categories")),
            "modifierGroups": list(
                self.session.iter_elements(f"{base}/modifier_groups", expand="modifiers")
            ),
        }
