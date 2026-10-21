"""Import the authorized domain mappings so relationships and Alembic share metadata."""

from app.models.audio_asset import AudioAsset
from app.models.edit_spec_revision import EditSpecRevision
from app.models.media_asset import MediaAsset
from app.models.preview_job import PreviewJob
from app.models.project import Project

__all__ = ["AudioAsset", "PreviewJob", "EditSpecRevision", "MediaAsset", "Project"]
