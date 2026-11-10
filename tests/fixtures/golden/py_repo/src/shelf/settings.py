"""The D1 trap, part 3: a bare call to set_value, with nothing imported.

At runtime this is a NameError; it must not be resolved to Store.set_value."""


def configure(debug):
    set_value("debug", debug)
