"""The D2 trap: a return embedding a call to a name defined nowhere."""


def build(parts):
    """Join parts."""
    return "-".join(parts)


def assemble(x):
    return build(missing_helper(x))
