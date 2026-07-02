from typing import List, Optional

class ShoppingCart:
    # BUG: Mutable default arguments are shared across all instances
    def __init__(self, items: List[str] = []):
        self.items = items

    def add_item(self, item: str):
        self.items.append(item)
