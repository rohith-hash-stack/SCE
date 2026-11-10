"""A near-duplicate pair: the same body with identifiers renamed (N1)."""


def total_weight(items):
    result = 0
    for item in items:
        if item.weight > 0:
            result += item.weight
    return result


def sum_masses(things):
    acc = 0
    for thing in things:
        if thing.weight > 0:
            acc += thing.weight
    return acc
