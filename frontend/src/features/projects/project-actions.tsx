import { useState } from 'react';
import { LoaderCircle, Pencil, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';
import { projectsApi, publicError, type Project } from '@/lib/api/projects';
import { ProjectForm } from './project-form';

export function ProjectActions({
  project,
  onUpdated,
  onDeleted,
  disabled = false,
}: {
  project: Project;
  onUpdated: () => void;
  onDeleted: () => void;
  disabled?: boolean;
}) {
  const [dialog, setDialog] = useState<'edit' | 'delete' | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function remove() {
    setBusy(true);
    setError('');
    try {
      await projectsApi.remove(project.id);
      setDialog(null);
      onDeleted();
    } catch (failure) {
      setError(publicError(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="project-actions">
        <Button
          variant="ghost"
          size="icon"
          aria-label={`Editar ${project.name}`}
          title="Editar projeto"
          onClick={() => setDialog('edit')}
          disabled={disabled}
        >
          <Pencil aria-hidden="true" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          aria-label={`Excluir ${project.name}`}
          title="Excluir projeto"
          onClick={() => {
            setError('');
            setDialog('delete');
          }}
          disabled={disabled}
        >
          <Trash2 aria-hidden="true" />
        </Button>
      </div>
      {dialog === 'edit' && (
        <ProjectForm
          project={project}
          onClose={() => setDialog(null)}
          onSave={async (input) => {
            await projectsApi.update(project.id, input);
            setDialog(null);
            onUpdated();
          }}
        />
      )}
      {dialog === 'delete' && (
        <Modal title="Excluir projeto?" busy={busy} onClose={() => setDialog(null)}>
          <p className="dialog-description">
            O projeto <strong>{project.name}</strong> deixará de aparecer na sua lista. Os vídeos e
            arquivos originais serão preservados. A restauração não está disponível nesta etapa.
          </p>
          {error && (
            <p className="feedback error" role="alert">
              {error}
            </p>
          )}
          <div className="dialog-actions">
            <Button variant="outline" onClick={() => setDialog(null)} disabled={busy}>
              Manter projeto
            </Button>
            <Button className="danger-button" onClick={() => void remove()} disabled={busy}>
              {busy && <LoaderCircle className="animate-spin" aria-hidden="true" />}
              {busy ? 'Excluindo…' : 'Confirmar exclusão'}
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
