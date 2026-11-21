from shelf.exporters import JsonExporter, export_all


def test_export_all_json():
    assert export_all(JsonExporter(), [["a"]]) == '[["a"]]'
