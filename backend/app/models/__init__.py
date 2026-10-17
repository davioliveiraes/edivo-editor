"""Import the two R02 mappings together so relationships and Alembic share metadata."""

from app.models.media_asset import MediaAsset
from app.models.project import Project

__all__ = ["MediaAsset", "Project"]
