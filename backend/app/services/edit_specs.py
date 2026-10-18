"""Serialize short revision transactions on the same project lock as soft deletion."""

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.edit_spec_revision import EditSpecRevision
from app.repositories.edit_specs import EditSpecRepository
from app.repositories.media import MediaRepository
from app.schemas.edit_spec import EditSpecPut, EditSpecResponse
from app.services.projects import ProjectService


def revision_response(revision: EditSpecRevision) -> EditSpecResponse:
    return EditSpecResponse(
        **revision.spec_json,
        id=revision.id,
        project_id=revision.project_id,
        revision_number=revision.revision_number,
        created_at=revision.created_at,
    )


class EditSpecService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = EditSpecRepository(session)

    def get(self, project_id: UUID) -> EditSpecResponse:
        ProjectService(self.session).get(project_id)
        revision = self.repository.current(project_id)
        if revision is None:
            raise AppError(
                404, "edit_spec_not_found", "Este projeto ainda não possui edição salva."
            )
        return revision_response(revision)

    def put(self, project_id: UUID, data: EditSpecPut) -> tuple[EditSpecResponse, bool]:
        # Lock before reading the current revision: concurrent writers based on
        # the same expected_revision cannot both append a new snapshot.
        ProjectService(self.session).get(project_id, for_update=True)
        current = self.repository.current(project_id)
        actual_revision = current.revision_number if current is not None else 0
        if data.expected_revision != actual_revision:
            raise AppError(
                409,
                "edit_spec_conflict",
                "A edição foi alterada. Recarregue a revisão atual antes de salvar.",
            )

        asset = MediaRepository(self.session).get_for_project(project_id, data.source_media_id)
        if asset is None:
            raise AppError(404, "media_not_found", "Mídia não encontrada neste projeto.")
        if asset.kind != "source_video" or asset.mime_detected != "video/mp4":
            raise AppError(422, "invalid_edit_spec", "Selecione uma fonte de vídeo MP4 válida.")
        minimum_ms = min(100, asset.duration_ms)
        if (
            data.trim.end_ms > asset.duration_ms
            or data.trim.end_ms - data.trim.start_ms < minimum_ms
        ):
            raise AppError(
                422,
                "invalid_edit_spec",
                "O trecho deve respeitar a duração da fonte e o intervalo mínimo permitido.",
            )

        plan = data.plan().model_dump(mode="json")
        if current is not None and current.spec_json == plan:
            # An identical explicit save is a no-op. The version check above
            # remains mandatory even when the submitted plan is identical.
            response = revision_response(current)
            self.session.commit()
            return response, False

        revision = EditSpecRevision(
            project_id=project_id,
            revision_number=actual_revision + 1,
            source_media_id=data.source_media_id,
            schema_version=data.schema_version,
            spec_json=plan,
        )
        self.repository.add(revision)
        self.session.flush()
        # Build a detached response while values are available; no response
        # serialization depends on a live session after commit/teardown.
        response = revision_response(revision)
        self.session.commit()
        return response, current is None
