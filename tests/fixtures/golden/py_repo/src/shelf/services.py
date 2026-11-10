"""A call that reaches QuerySet.get only through the manager proxy."""
from shelf.models.book import Book


def find_book(pk):
    return Book.objects.get(pk=pk)
