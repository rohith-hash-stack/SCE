import celery

from app import OrderController

celery_app = celery.Celery("orders")


@celery_app.task
def handle_payment_completed(payload):
    print("payment completed", payload)


def bootstrap(session, payment_client, event_producer):
    return OrderController(session, payment_client, event_producer, tax_rate=0.08)
