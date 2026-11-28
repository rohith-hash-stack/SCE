"""A base class, two overriding subclasses, and calls on the base type."""
import json


class Exporter:
    def export(self, rows):
        """Export rows; the base exporter returns them unchanged."""
        return rows


class JsonExporter(Exporter):
    def export(self, rows):
        return json.dumps(rows)


class CsvExporter(Exporter):
    def export(self, rows):
        return "\n".join(",".join(r) for r in rows)


def export_all(exporter: Exporter, rows):
    """Export through whichever exporter is passed (virtual dispatch)."""
    return exporter.export(rows)
