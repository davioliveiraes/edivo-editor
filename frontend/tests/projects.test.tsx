import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ProjectsWorkspace } from '@/features/projects/projects-workspace';
import { project, json, page } from './fixtures';

beforeEach(() => window.history.replaceState(null, '', '/'));

describe('Project management', () => {
  it('distinguishes loading and an empty project list', async () => {
    let complete: (response: Response) => void = () => {};
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            complete = resolve;
          }),
      ),
    );
    render(<ProjectsWorkspace />);
    expect(screen.getByText('Carregando projetos…')).toBeVisible();
    await act(async () => complete(json(page([]))));
    expect(await screen.findByText('Sua próxima história começa aqui.')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Criar primeiro projeto' })).toBeEnabled();
  });

  it('presents a sanitized failure and allows a successful retry', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error('private-stack'))
      .mockResolvedValueOnce(json(page([project])));
    vi.stubGlobal('fetch', fetchMock);
    render(<ProjectsWorkspace />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Verifique a conexão');
    expect(screen.queryByText('private-stack')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Recarregar projetos' }));
    expect(await screen.findByRole('link', { name: new RegExp(project.name) })).toBeVisible();
  });

  it('creates a project with trimmed inputs and opens its detail', async () => {
    const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
      if (options?.method === 'POST') return json(project, 201);
      if (url.includes('/media?')) return json(page([]));
      if (url.endsWith(project.id)) return json(project);
      return json(page([]));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<ProjectsWorkspace />);
    await userEvent.click(screen.getByRole('button', { name: 'Novo projeto' }));
    const dialog = screen.getByRole('dialog', { name: 'Novo projeto' });
    expect(within(dialog).getByLabelText(/Nome do projeto/)).toHaveFocus();
    await userEvent.type(within(dialog).getByLabelText(/Nome do projeto/), `  ${project.name}  `);
    await userEvent.type(within(dialog).getByLabelText(/Finalidade/), ` ${project.purpose} `);
    await userEvent.selectOptions(within(dialog).getByLabelText(/Formato alvo/), '9:16');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Criar projeto' }));
    expect(await screen.findByRole('heading', { name: project.name })).toBeVisible();
    expect(window.location.hash).toBe(`#/projects/${project.id}`);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/projects',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          name: project.name,
          purpose: project.purpose,
          target_preset: '9:16',
        }),
      }),
    );
    expect(screen.getByRole('heading', { name: 'Biblioteca de mídia' })).toBeVisible();
  });

  it('rejects a whitespace-only name without issuing a creation request', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(page([])));
    vi.stubGlobal('fetch', fetchMock);
    render(<ProjectsWorkspace />);
    await userEvent.click(screen.getByRole('button', { name: 'Novo projeto' }));
    await userEvent.type(screen.getByLabelText(/Nome do projeto/), '   ');
    await userEvent.click(screen.getByRole('button', { name: 'Criar projeto' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Informe um nome');
    expect(fetchMock.mock.calls.every(([, options]) => options?.method !== 'POST')).toBe(true);
  });

  it('keeps the creation form and displays a service failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, options?: RequestInit) =>
        options?.method === 'POST' ? json({ code: 'database_unavailable' }, 503) : json(page([])),
      ),
    );
    render(<ProjectsWorkspace />);
    await userEvent.click(screen.getByRole('button', { name: 'Novo projeto' }));
    await userEvent.type(screen.getByLabelText(/Nome do projeto/), 'Teste');
    await userEvent.click(screen.getByRole('button', { name: 'Criar projeto' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('temporariamente indisponível');
    expect(screen.getByRole('dialog')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Criar projeto' })).toBeEnabled();
  });

  it('updates name, purpose and optional preset and refreshes the list', async () => {
    let updated = false;
    const fetchMock = vi.fn(async (_url: string, options?: RequestInit) => {
      if (options?.method === 'PATCH') {
        updated = true;
        return json({
          ...project,
          name: 'Novo nome',
          purpose: 'Novo propósito',
          target_preset: null,
        });
      }
      return json(page([{ ...project, name: updated ? 'Novo nome' : project.name }]));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<ProjectsWorkspace />);
    await userEvent.click(await screen.findByRole('button', { name: `Editar ${project.name}` }));
    const name = screen.getByLabelText(/Nome do projeto/);
    await userEvent.clear(name);
    await userEvent.type(name, 'Novo nome');
    const purpose = screen.getByLabelText(/Finalidade/);
    await userEvent.clear(purpose);
    await userEvent.type(purpose, 'Novo propósito');
    await userEvent.selectOptions(screen.getByLabelText(/Formato alvo/), '');
    await userEvent.click(screen.getByRole('button', { name: 'Salvar alterações' }));
    expect(await screen.findByRole('link', { name: 'Abrir projeto Novo nome' })).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/projects/${project.id}`,
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ name: 'Novo nome', purpose: 'Novo propósito', target_preset: null }),
      }),
    );
    expect(screen.getByText('Projeto atualizado.')).toBeVisible();
  });

  it('requires explicit confirmation before a soft delete and preserves the wording about originals', async () => {
    let deleted = false;
    const fetchMock = vi.fn(async (_url: string, options?: RequestInit) => {
      if (options?.method === 'DELETE') {
        deleted = true;
        return new Response(null, { status: 204 });
      }
      return json(page(deleted ? [] : [project]));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<ProjectsWorkspace />);
    await userEvent.click(await screen.findByRole('button', { name: `Excluir ${project.name}` }));
    const dialog = screen.getByRole('dialog', { name: 'Excluir projeto?' });
    expect(dialog).toHaveTextContent('Os vídeos e arquivos originais serão preservados');
    expect(deleted).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Manter projeto' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(deleted).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: `Excluir ${project.name}` }));
    await userEvent.click(screen.getByRole('button', { name: 'Confirmar exclusão' }));
    expect(await screen.findByText('Sua próxima história começa aqui.')).toBeVisible();
    expect(
      screen.getByText('Projeto excluído. Os arquivos originais foram preservados.'),
    ).toBeVisible();
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'DELETE')).toHaveLength(
      1,
    );
  });

  it('requests deterministic offset pagination and disables the end of the list', async () => {
    const many = Array.from({ length: 20 }, (_, index) => ({
      ...project,
      id: `${project.id}-${index}`,
      name: `Projeto ${index}`,
    }));
    const fetchMock = vi.fn(async (url: string) =>
      json(
        url.includes('offset=20')
          ? page([{ ...project, name: 'Último projeto' }], 21, 20)
          : page(many, 21),
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    render(<ProjectsWorkspace />);
    await screen.findByRole('link', { name: 'Abrir projeto Projeto 0' });
    expect(screen.getByRole('button', { name: 'Página anterior de projetos' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Próxima página de projetos' }));
    expect(await screen.findByRole('link', { name: 'Abrir projeto Último projeto' })).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/projects?limit=20&offset=20',
      expect.any(Object),
    );
    expect(screen.getByRole('button', { name: 'Próxima página de projetos' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Página anterior de projetos' }));
    expect(await screen.findByRole('link', { name: 'Abrir projeto Projeto 0' })).toBeVisible();
  });

  it('opens a detail from a real link and returns to the list with hash navigation', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        json(
          url.endsWith(project.id) ? project : url.includes('/media?') ? page([]) : page([project]),
        ),
      ),
    );
    render(<ProjectsWorkspace />);
    await userEvent.click(await screen.findByRole('link', { name: new RegExp(project.name) }));
    expect(await screen.findByRole('heading', { name: project.name })).toBeVisible();
    await userEvent.click(screen.getByRole('link', { name: 'Todos os projetos' }));
    expect(await screen.findByRole('heading', { name: 'Seus projetos.' })).toBeVisible();
    expect(await screen.findByRole('link', { name: new RegExp(project.name) })).toBeVisible();
  });

  it('handles a missing or deleted project without showing its library', async () => {
    window.history.replaceState(null, '', `/#/projects/${project.id}`);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ code: 'project_not_found' }, 404)));
    render(<ProjectsWorkspace />);
    expect(await screen.findByRole('alert')).toHaveTextContent('não está mais disponível');
    expect(screen.queryByRole('heading', { name: 'Biblioteca de mídia' })).not.toBeInTheDocument();
  });

  it('supports browser hash navigation from an external change', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => json(url.endsWith(project.id) ? project : page([]))),
    );
    render(<ProjectsWorkspace />);
    await screen.findByText('Sua próxima história começa aqui.');
    act(() => {
      window.history.replaceState(null, '', `/#/projects/${project.id}`);
      fireEvent(window, new HashChangeEvent('hashchange'));
    });
    await waitFor(() => expect(screen.getByRole('heading', { name: project.name })).toBeVisible());
  });
});
