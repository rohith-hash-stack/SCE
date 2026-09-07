"""Order persistence."""
from __future__ import annotations

import sqlalchemy


class OrderRepository:
    def __init__(self, session):
        self.session = session

    def save(self, order):
        """Persist an order. A #db_write path - the architectural rules for
        this codebase require an auth guard before any call reaches here."""
        self.session.add(order)
        self.session.commit()
