import { ArrowLeft, ArrowRight } from 'lucide-react';
import { Button } from '@/components/ui/button';

export function Pagination({
  total,
  offset,
  count,
  label,
  onChange,
}: {
  total: number;
  offset: number;
  count: number;
  label: string;
  onChange: (offset: number) => void;
}) {
  if (total <= 20 && offset === 0) return null;
  return (
    <nav className="pagination" aria-label={`Paginação de ${label}`}>
      <span>
        {total === 0 ? '0' : `${Math.min(offset + 1, total)}–${Math.min(offset + count, total)}`} de{' '}
        {total} {label}
      </span>
      <div>
        <Button
          variant="outline"
          aria-label={`Página anterior de ${label}`}
          disabled={offset === 0}
          onClick={() => onChange(Math.max(0, offset - 20))}
        >
          <ArrowLeft aria-hidden="true" />
          Anterior
        </Button>
        <Button
          variant="outline"
          aria-label={`Próxima página de ${label}`}
          disabled={offset + count >= total}
          onClick={() => onChange(offset + 20)}
        >
          Próxima
          <ArrowRight aria-hidden="true" />
        </Button>
      </div>
    </nav>
  );
}
