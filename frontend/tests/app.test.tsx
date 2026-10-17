import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { App } from '@/app/app';

const healthy = () =>
  new Response(JSON.stringify({ status: 'ok', service: 'edivo-api', version: '0.1.0' }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });

describe('Edivo foundation interface', () => {
  it('presents the product, current scope and a distinct loading state', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise(() => {})),
    );
    render(<App />);

    expect(screen.getByRole('link', { name: 'Edivo — início' })).toBeVisible();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Boas histórias.');
    expect(screen.getByText(/recursos do editor serão disponibilizados/)).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Verificando conexão' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Verificando…' })).toBeDisabled();
  });

  it('fetches the relative endpoint and presents the real response version', async () => {
    const fetchMock = vi.fn().mockResolvedValue(healthy());
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    expect(await screen.findByRole('heading', { name: 'API disponível' })).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/system/health', {
      signal: expect.any(AbortSignal),
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });
    await userEvent.click(screen.getByText('Detalhes da conexão'));
    expect(screen.getByText('0.1.0')).toBeVisible();
    expect(screen.getByText(/não monitora banco de dados, storage ou worker/)).toBeVisible();
  });

  it('handles network failure without exposing technical error details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('private-internal-details')));
    render(<App />);

    expect(await screen.findByRole('heading', { name: 'API indisponível' })).toBeVisible();
    expect(screen.getByText(/Verifique se a API está em execução/)).toBeVisible();
    expect(screen.queryByText(/private-internal-details/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Tentar novamente' })).toBeEnabled();
  });

  it('treats a failed HTTP status as unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 503 })));
    render(<App />);

    expect(await screen.findByRole('heading', { name: 'API indisponível' })).toBeVisible();
  });

  it('rejects an unexpected response instead of claiming connectivity', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ok' }))),
    );
    render(<App />);

    expect(await screen.findByRole('heading', { name: 'API indisponível' })).toBeVisible();
  });

  it('recovers from an outage when the user retries', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(healthy());
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    await userEvent.click(await screen.findByRole('button', { name: 'Tentar novamente' }));
    expect(await screen.findByRole('heading', { name: 'API disponível' })).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('aborts a slow request after five seconds and presents an outage', async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: string, options: RequestInit) =>
          new Promise((_, reject) => {
            options.signal?.addEventListener('abort', () =>
              reject(new DOMException('Timeout', 'AbortError')),
            );
          }),
      ),
    );
    try {
      render(<App />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });
      expect(screen.getByRole('heading', { name: 'API indisponível' })).toBeVisible();
    } finally {
      vi.useRealTimers();
    }
  });

  it('toggles and persists the theme with an accessible control', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(healthy()));
    render(<App />);
    await screen.findByRole('heading', { name: 'API disponível' });

    await userEvent.click(screen.getByRole('button', { name: 'Ativar tema escuro' }));
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    expect(localStorage.getItem('edivo-theme')).toBe('dark');
    await userEvent.click(screen.getByRole('button', { name: 'Ativar tema claro' }));
    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
    expect(localStorage.getItem('edivo-theme')).toBe('light');
  });

  it('restores the saved theme at startup', async () => {
    localStorage.setItem('edivo-theme', 'dark');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(healthy()));
    render(<App />);

    await waitFor(() => expect(document.documentElement).toHaveAttribute('data-theme', 'dark'));
    expect(screen.getByRole('button', { name: 'Ativar tema claro' })).toBeVisible();
    await screen.findByRole('heading', { name: 'API disponível' });
  });
});
