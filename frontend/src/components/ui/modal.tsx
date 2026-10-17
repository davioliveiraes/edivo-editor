import { useEffect, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { Button } from './button';

export function Modal({
  title,
  children,
  busy,
  onClose,
}: {
  title: string;
  children: ReactNode;
  busy: boolean;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    const previousFocus = document.activeElement;
    if (dialog?.showModal) dialog.showModal();
    else dialog?.setAttribute('open', '');
    (
      dialog?.querySelector<HTMLElement>('input') ?? dialog?.querySelector<HTMLElement>('button')
    )?.focus();
    return () => {
      dialog?.close?.();
      if (previousFocus instanceof HTMLElement) previousFocus.focus();
    };
  }, []);

  return (
    <dialog
      ref={dialogRef}
      className="edivo-dialog"
      aria-labelledby="modal-title"
      aria-modal="true"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="dialog-heading">
        <h2 id="modal-title">{title}</h2>
        <Button
          variant="ghost"
          size="icon"
          onClick={onClose}
          disabled={busy}
          aria-label="Fechar diálogo"
        >
          <X aria-hidden="true" />
        </Button>
      </div>
      {children}
    </dialog>
  );
}
