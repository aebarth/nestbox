from models import Source

from .base import Connector, RemoteFile  # noqa: F401


def get(source: Source) -> Connector:
    if source.kind == "local":
        from .local import LocalConnector

        return LocalConnector(source)
    if source.kind == "gdrive":
        from .gdrive import GDriveConnector

        return GDriveConnector(source)
    if source.kind == "graph_drive":
        from .graph import GraphConnector

        return GraphConnector(source)
    raise ValueError(f"Unknown source kind: {source.kind}")
