import { useEffect, useState } from 'react';
import { ArrowUpRight, Check, LoaderCircle, RefreshCw, Server, WifiOff } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { getApiHealth, HEALTH_ENDPOINT, type ApiHealth } from '@/lib/api/system';

type Connection =
  | { status: 'loading' }
  | { status: 'available'; health: ApiHealth; checkedAt: Date }
  | { status: 'unavailable' };

export function ApiStatus() {
  const [connection, setConnection] = useState<Connection>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const timeout = window.setTimeout(() => controller.abort(), 5_000);

    void getApiHealth(controller.signal)
      .then((health) => {
        if (active) setConnection({ status: 'available', health, checkedAt: new Date() });
      })
      .catch(() => {
        if (active) setConnection({ status: 'unavailable' });
      })
      .finally(() => window.clearTimeout(timeout));

    return () => {
      active = false;
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [attempt]);

  function checkConnection() {
    setConnection({ status: 'loading' });
    setAttempt((current) => current + 1);
  }

  const loading = connection.status === 'loading';
  const available = connection.status === 'available';
  const StatusIcon = loading ? LoaderCircle : available ? Check : WifiOff;

  return (
    <section className="connection-card" aria-labelledby="connection-title">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2.5 text-sm font-medium text-muted-foreground">
          <Server size={16} aria-hidden="true" />
          <h2 id="connection-title">Conexão com a API</h2>
        </div>
        <span className="connection-label">LOCAL</span>
      </div>

      <div
        className="connection-state"
        data-state={connection.status}
        role="status"
        aria-live="polite"
      >
        <div className="status-symbol" aria-hidden="true">
          <StatusIcon
            className={loading ? 'animate-spin' : undefined}
            size={26}
            strokeWidth={1.8}
          />
        </div>
        <h3>
          {loading ? 'Verificando conexão' : available ? 'API disponível' : 'API indisponível'}
        </h3>
        <p>
          {loading
            ? 'Conectando ao serviço local do Edivo…'
            : available
              ? 'Tudo certo. A interface está conectada ao serviço local do Edivo.'
              : 'Não foi possível conectar ao serviço local. Verifique se a API está em execução e tente novamente.'}
        </p>
      </div>

      <div className="connection-action">
        <Button variant="outline" className="w-full" onClick={checkConnection} disabled={loading}>
          <RefreshCw aria-hidden="true" className={loading ? 'animate-spin' : undefined} />
          {loading ? 'Verificando…' : available ? 'Verificar novamente' : 'Tentar novamente'}
        </Button>
        <p className="check-time">
          {available ? (
            <>
              Verificado às{' '}
              <time dateTime={connection.checkedAt.toISOString()}>
                {connection.checkedAt.toLocaleTimeString('pt-BR', {
                  hour: '2-digit',
                  minute: '2-digit',
                  second: '2-digit',
                })}
              </time>
            </>
          ) : (
            'Consulta apenas a disponibilidade da API.'
          )}
        </p>
      </div>

      <details className="connection-details">
        <summary>
          Detalhes da conexão <ArrowUpRight size={14} aria-hidden="true" />
        </summary>
        <dl>
          <div>
            <dt>Endpoint</dt>
            <dd>
              <code>{HEALTH_ENDPOINT}</code>
            </dd>
          </div>
          {available && (
            <div>
              <dt>Versão da API</dt>
              <dd>{connection.health.version}</dd>
            </div>
          )}
        </dl>
        <p>Este indicador não monitora banco de dados, storage ou worker.</p>
      </details>
    </section>
  );
}
