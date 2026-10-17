export const HEALTH_ENDPOINT = '/api/v1/system/health';

export interface ApiHealth {
  status: 'ok';
  service: 'edivo-api';
  version: string;
}

export async function getApiHealth(signal: AbortSignal): Promise<ApiHealth> {
  const response = await fetch(HEALTH_ENDPOINT, {
    signal,
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  });

  if (!response.ok) throw new Error('API_UNAVAILABLE');

  const body: unknown = await response.json();
  if (
    typeof body !== 'object' ||
    body === null ||
    !('status' in body) ||
    body.status !== 'ok' ||
    !('service' in body) ||
    body.service !== 'edivo-api' ||
    !('version' in body) ||
    typeof body.version !== 'string' ||
    body.version.trim().length === 0
  ) {
    throw new Error('INVALID_HEALTH_RESPONSE');
  }

  return { status: body.status, service: body.service, version: body.version };
}
