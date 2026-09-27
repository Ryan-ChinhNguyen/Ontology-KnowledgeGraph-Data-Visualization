from ontology_shared.storage import FileStorage, LocalFileStorage

from app.core.config import settings

#: Reads the files the API wrote and writes parsed tables beside them, so both
#: services must be pointed at the same place.
storage: FileStorage = LocalFileStorage(settings.upload_dir)
