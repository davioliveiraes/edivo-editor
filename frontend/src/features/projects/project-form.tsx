import { useState, type FormEvent } from 'react';
import { LoaderCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';
import {
  publicError,
  type Project,
  type ProjectInput,
  type TargetPreset,
} from '@/lib/api/projects';

export function ProjectForm({
  project,
  onClose,
  onSave,
}: {
  project?: Project;
  onClose: () => void;
  onSave: (input: ProjectInput) => Promise<void>;
}) {
  const [name, setName] = useState(project?.name ?? '');
  const [purpose, setPurpose] = useState(project?.purpose ?? '');
  const [preset, setPreset] = useState<TargetPreset | ''>(project?.target_preset ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim()) {
      setError('Informe um nome para o projeto.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await onSave({
        name: name.trim(),
        purpose: purpose.trim() || null,
        target_preset: preset || null,
      });
    } catch (failure) {
      setError(publicError(failure));
      setBusy(false);
    }
  }

  return (
    <Modal title={project ? 'Editar projeto' : 'Novo projeto'} busy={busy} onClose={onClose}>
      <p className="dialog-description">
        Dê um nome à sua próxima história. Os detalhes podem mudar depois.
      </p>
      <form className="project-form" onSubmit={(event) => void submit(event)}>
        <label htmlFor="project-name">
          Nome do projeto <span aria-hidden="true">*</span>
        </label>
        <input
          id="project-name"
          name="name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={120}
          required
          disabled={busy}
          placeholder="Ex.: Campanha de primavera"
          aria-describedby={error ? 'project-form-error' : undefined}
        />
        <label htmlFor="project-purpose">
          Finalidade <span>opcional</span>
        </label>
        <textarea
          id="project-purpose"
          name="purpose"
          value={purpose}
          onChange={(event) => setPurpose(event.target.value)}
          maxLength={500}
          rows={3}
          disabled={busy}
          placeholder="O que você quer contar com este projeto?"
        />
        <label htmlFor="project-preset">
          Formato alvo <span>opcional</span>
        </label>
        <select
          id="project-preset"
          value={preset}
          onChange={(event) => setPreset(event.target.value as TargetPreset | '')}
          disabled={busy}
          aria-describedby="preset-help"
        >
          <option value="">Ainda não definido</option>
          <option value="9:16">9:16 · Vertical</option>
          <option value="1:1">1:1 · Quadrado</option>
          <option value="4:5">4:5 · Retrato</option>
          <option value="16:9">16:9 · Horizontal</option>
        </select>
        <p id="preset-help" className="field-help">
          Uma referência para o projeto. O vídeo original não é transformado.
        </p>
        {error && (
          <p className="feedback error" id="project-form-error" role="alert">
            {error}
          </p>
        )}
        <div className="dialog-actions">
          <Button variant="outline" onClick={onClose} disabled={busy}>
            Cancelar
          </Button>
          <Button type="submit" disabled={busy}>
            {busy && <LoaderCircle className="animate-spin" aria-hidden="true" />}
            {busy ? 'Salvando…' : project ? 'Salvar alterações' : 'Criar projeto'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
