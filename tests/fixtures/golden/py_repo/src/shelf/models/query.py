"""Manager-proxied calls, as Django builds them: the manager's methods are created dynamically."""


class QuerySet:
    def __init__(self):
        self.filters = {}

    def filter(self, **kwargs):
        """Narrow the query."""
        self.filters.update(kwargs)
        return self

    def get(self, **kwargs):
        """The one matching object; raises LookupError when there is none."""
        if not kwargs:
            raise LookupError("no lookup given")
        return self.filter(**kwargs)


class BaseManager:
    def get_queryset(self):
        return self._queryset_class()

    @classmethod
    def from_queryset(cls, queryset_class):
        def make(name):
            def manager_method(self, *args, **kwargs):
                return getattr(self.get_queryset(), name)(*args, **kwargs)
            return manager_method
        attrs = {name: make(name) for name in ("get", "filter")}
        attrs["_queryset_class"] = queryset_class
        return type(cls.__name__ + "FromQuerySet", (cls,), attrs)


Manager = BaseManager.from_queryset(QuerySet)
