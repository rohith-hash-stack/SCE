import sqlalchemy


class OrderRepository:
    def __init__(self, session):
        self.session = session

    def save(self, order):
        self.session.add(order)
        self.session.commit()
