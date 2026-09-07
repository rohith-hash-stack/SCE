"""Persistence layer for orders.

Every write goes through SQLAlchemy's session API; `save` and `find_by_id`
are the two methods actually on the checkout path, but a real repository
class accumulates a long tail of read/admin helpers over time, which this
module reproduces for benchmark realism.
"""
from __future__ import annotations

import logging

import sqlalchemy

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 25


class OrderNotFoundError(Exception):
    pass


class OrderRepository:
    def __init__(self, session):
        self.session = session

    def save(self, order):
        """Persist a new or updated order and commit the transaction."""
        self.session.add(order)
        self.session.commit()
        logger.debug("saved order %s", order.order_id)

    def find_by_id(self, order_id):
        result = self.session.query(order_id).first()
        if result is None:
            raise OrderNotFoundError(order_id)
        return result

    def find_by_status(self, status, limit=DEFAULT_PAGE_SIZE, offset=0):
        """List orders in a given status, paginated - used by the admin
        dashboard, not by the checkout flow itself."""
        query = self.session.filter(status=status)
        return query.all()[offset : offset + limit]

    def find_recent_for_customer(self, customer_id, days=30):
        cutoff = self._compute_cutoff(days)
        return self.session.filter(customer_id=customer_id, created_after=cutoff).all()

    def count_by_status(self, status):
        return self.session.filter(status=status).all().__len__()

    def delete(self, order_id):
        """Hard-delete an order record. Reserved for GDPR erasure requests;
        should almost never be called from normal application code."""
        order = self.find_by_id(order_id)
        self.session.execute(f"delete from orders where id = {order_id!r}")
        self.session.commit()
        logger.warning("hard-deleted order %s", order_id)
        return order

    def _compute_cutoff(self, days):
        import datetime

        return datetime.datetime.utcnow() - datetime.timedelta(days=days)
