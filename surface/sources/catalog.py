from surface.sources.mesh_input import normalize_mesh
from surface.sources.field_input import normalize_fields
from surface.sources.revisions import RevisionRepository


class SourceCatalog:
    def __init__(self, store):
        self.store = store
        self.revisions = RevisionRepository(store)

    def put_source(self, request_id, mesh, fields):
        if type(request_id) is not str or not request_id:
            raise ValueError("nonempty source request ID required")
        mesh = normalize_mesh(mesh)
        fields = normalize_fields(mesh, fields)
        content = {"mesh": mesh.to_record(), "fields": fields.to_record()}
        with self.store.transaction():
            return self.revisions.append(request_id, content)

    def read_source(self, revision_id):
        return self.revisions.read(revision_id).to_record()

    def current_revision(self):
        return self.revisions.current()
