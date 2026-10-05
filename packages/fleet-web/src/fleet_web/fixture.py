"""Render documents from the container's recorded-fixture adapter."""
from fleet_web.library import render_document


class FixtureLibrary:
    def __init__(self, fixture, *, container):
        self.reader = container.fixture_library(fixture=fixture)
        self.roots = self.reader.roots

    def root(self, project):
        return self.reader.root(project)

    def list(self):
        return self.reader.list()

    def read(self, project, document_id):
        document = self.reader.read(project, document_id)
        return render_document(document, document_id) if document is not None else None

    def read_asset(self, project, document_id, asset_path):
        return self.reader.read_asset(project, document_id, asset_path)
