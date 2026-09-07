import sqlalchemy


class OrderNotFoundError(Exception):
    pass


class OrderRepository:
    def __init__(self, session):
        self.session = session

    def mark_paid(self, cart_id, receipt_id):
        order = self.session.query(cart_id)
        if order is None:
            raise OrderNotFoundError(cart_id)
        order.status = "PAID"
        self.session.commit()

    def find_open_orders(self, user_id):
        return self.session.filter(user_id=user_id).all()
