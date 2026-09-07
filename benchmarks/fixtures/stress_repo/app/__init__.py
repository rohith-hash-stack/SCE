# Barrel re-export: external consumers import the controller from the
# package root rather than reaching into `app.controllers.orders` directly.
# (Deliberately NOT used by sibling modules within `app` itself, which
# import each other's fully-qualified submodule paths directly - that's
# the realistic pattern, and it keeps this re-export off the critical path
# of `OrderController.process_order`'s own call chain.)
from app.controllers.orders import OrderController, create_order_endpoint

__all__ = ["OrderController", "create_order_endpoint"]
