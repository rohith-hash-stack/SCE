class Forbidden(PermissionError):
    pass


class PayloadValidationError(Exception):
    pass


class PaymentDeclinedError(Exception):
    pass
