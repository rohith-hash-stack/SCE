from shelf.core.fmt import render


def test_render_pads():
    assert render([["a"]], width=3) == "a  "
