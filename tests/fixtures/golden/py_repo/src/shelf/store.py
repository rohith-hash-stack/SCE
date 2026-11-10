"""The D1 trap, part 2: a class method with the same name as values.set_value."""


class Store:
    def __init__(self):
        self._data = {}
        self.writes = 0

    def set_value(self, key, value):
        """Set a value in this store (a state change)."""
        self._data[key] = value
        self.writes += 1
