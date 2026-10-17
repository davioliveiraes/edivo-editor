import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowUpRight, FolderOpen, LoaderCircle, Plus, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { projectsApi, publicError, type Page, type Project } from '@/lib/api/projects';
import { useResource } from '@/lib/api/use-resource';
import { formatDate } from '@/lib/format';
import { MediaLibrary } from '@/features/media/media-library';
import { ProjectActions } from './project-actions';
import { ProjectForm } from './project-form';
import { Pagination } from './pagination';

const projectHash =
  /^#\/projects\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i;
const currentProject = () => projectHash.exec(window.location.hash)?.[1] ?? null;

function ProjectList({ onCreated }: { onCreated: (project: Project) => void }) {
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState('');
  const { data, loading, error, reload } = useResource<Page<Project>>(`?limit=20&offset=${offset}`);

  return (
    <>
      <div className="workspace-heading">
        <div>
          <p className="section-kicker">ORGANIZE O COMEÇO</p>
          <h2>
            Seus projetos<span className="accent-word">.</span>
          </h2>
          <p>Um lugar para cada ideia e seus vídeos originais.</p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <Plus aria-hidden="true" />
          Novo projeto
        </Button>
      </div>
      {notice && (
        <p className="feedback success" role="status">
          {notice}
        </p>
      )}
      {loading ? (
        <div className="workspace-state" role="status">
          <LoaderCircle className="animate-spin" aria-hidden="true" />
          <p>Carregando projetos…</p>
        </div>
      ) : error ? (
        <div className="workspace-state">
          <p role="alert">{publicError(error)}</p>
          <Button variant="outline" onClick={reload}>
            <RefreshCw aria-hidden="true" />
            Recarregar projetos
          </Button>
        </div>
      ) : data?.items.length === 0 ? (
        <div className="workspace-state empty-state">
          <FolderOpen size={34} strokeWidth={1.3} aria-hidden="true" />
          <h3>Sua próxima história começa aqui.</h3>
          <p>Crie um projeto para reunir seus vídeos e conhecer cada arquivo.</p>
          <Button variant="outline" onClick={() => setCreating(true)}>
            <Plus aria-hidden="true" />
            Criar primeiro projeto
          </Button>
        </div>
      ) : (
        <>
          <div className="project-list" aria-label="Projetos ativos">
            {data?.items.map((project, index) => (
              <article className="project-row" key={project.id}>
                <span className="project-index" aria-hidden="true">
                  {String(offset + index + 1).padStart(2, '0')}
                </span>
                <a
                  className="project-main"
                  href={`#/projects/${project.id}`}
                  aria-label={`Abrir projeto ${project.name}`}
                >
                  <h3>
                    {project.name}
                    <ArrowUpRight size={17} aria-hidden="true" />
                  </h3>
                  <p>{project.purpose || 'Uma ideia esperando seus primeiros vídeos.'}</p>
                  <div className="project-meta">
                    <time dateTime={project.created_at}>{formatDate(project.created_at)}</time>
                    {project.target_preset && (
                      <span className="preset-badge">{project.target_preset}</span>
                    )}
                  </div>
                </a>
                <ProjectActions
                  project={project}
                  onUpdated={() => {
                    setNotice('Projeto atualizado.');
                    reload();
                  }}
                  onDeleted={() => {
                    setNotice('Projeto excluído. Os arquivos originais foram preservados.');
                    if (data.items.length === 1 && offset > 0) setOffset(Math.max(0, offset - 20));
                    else reload();
                  }}
                />
              </article>
            ))}
          </div>
          {data && (
            <Pagination
              total={data.total}
              offset={offset}
              count={data.items.length}
              label="projetos"
              onChange={setOffset}
            />
          )}
        </>
      )}
      {creating && (
        <ProjectForm
          onClose={() => setCreating(false)}
          onSave={async (input) => {
            const project = await projectsApi.create(input);
            setCreating(false);
            onCreated(project);
          }}
        />
      )}
    </>
  );
}

function ProjectDetail({ id, onDeleted }: { id: string; onDeleted: () => void }) {
  const { data: project, error, loading, reload } = useResource<Project>(`/${id}`);
  const [notice, setNotice] = useState('');
  const [uploading, setUploading] = useState(false);

  return (
    <>
      <a
        className={`back-link${uploading ? ' disabled-link' : ''}`}
        href="#/projects"
        aria-disabled={uploading || undefined}
        onClick={(event) => {
          if (uploading) event.preventDefault();
        }}
      >
        <ArrowLeft size={16} aria-hidden="true" />
        Todos os projetos
      </a>
      {loading ? (
        <div className="workspace-state" role="status">
          <LoaderCircle className="animate-spin" aria-hidden="true" />
          <p>Carregando projeto…</p>
        </div>
      ) : error || !project ? (
        <div className="workspace-state">
          <p role="alert">{publicError(error)}</p>
          <Button variant="outline" onClick={reload}>
            Tentar carregar projeto novamente
          </Button>
        </div>
      ) : (
        <>
          <div className="workspace-heading detail-heading">
            <div>
              <p className="section-kicker">PROJETO</p>
              <h2>{project.name}</h2>
              <p>
                {project.purpose || 'Adicione seus vídeos para começar a organizar esta ideia.'}
              </p>
              <div className="project-meta">
                <span>
                  Criado em{' '}
                  <time dateTime={project.created_at}>{formatDate(project.created_at)}</time>
                </span>
                {project.target_preset && (
                  <span className="preset-badge">Formato alvo {project.target_preset}</span>
                )}
              </div>
            </div>
            <ProjectActions
              project={project}
              disabled={uploading}
              onUpdated={() => {
                setNotice('Projeto atualizado.');
                reload();
              }}
              onDeleted={onDeleted}
            />
          </div>
          {notice && (
            <p className="feedback success" role="status">
              {notice}
            </p>
          )}
          <MediaLibrary projectId={id} onBusyChange={setUploading} />
        </>
      )}
    </>
  );
}

export function ProjectsWorkspace() {
  const [projectId, setProjectId] = useState(currentProject);
  const [notice, setNotice] = useState('');
  useEffect(() => {
    const revealWorkspace = () => {
      if (window.location.hash.startsWith('#/projects'))
        document.getElementById('projects-workspace')?.scrollIntoView?.({ block: 'start' });
    };
    const navigate = () => {
      const next = currentProject();
      setProjectId(next);
      revealWorkspace();
    };
    revealWorkspace();
    window.addEventListener('hashchange', navigate);
    return () => window.removeEventListener('hashchange', navigate);
  }, []);

  return (
    <section
      className="projects-workspace"
      id="projects-workspace"
      aria-label="Gerenciamento de projetos"
    >
      {notice && !projectId && (
        <p className="feedback success" role="status">
          {notice}
        </p>
      )}
      {projectId ? (
        <ProjectDetail
          key={projectId}
          id={projectId}
          onDeleted={() => {
            setNotice('Projeto excluído. Os arquivos originais foram preservados.');
            window.location.hash = '/projects';
          }}
        />
      ) : (
        <ProjectList
          onCreated={(project) => {
            setNotice('');
            window.location.hash = `/projects/${project.id}`;
          }}
        />
      )}
    </section>
  );
}
