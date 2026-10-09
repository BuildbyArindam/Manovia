/**
 * The modal every dialog in the app is built from.
 *
 * Accessibility contract (asserted in `Modal.test.tsx`):
 * - `role="dialog"` + `aria-modal="true"`, labelled and described by the caller.
 * - focus moves into the dialog on open and back to whatever opened it on close.
 * - Tab and Shift+Tab cycle inside the dialog (a real trap, not a suggestion).
 * - Escape closes.
 * - the page behind does not scroll while the dialog is open.
 */

import {
  useCallback,
  useEffect,
  useRef,
  type KeyboardEvent,
  type MouseEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import { getFocusableElements } from "@/lib/focus";

export interface ModalProps {
  /** id of the element naming the dialog. */
  labelledBy: string;
  /** id of the element describing the dialog. */
  describedBy?: string;
  onClose: () => void;
  children: ReactNode;
  testId?: string;
}

export function Modal({
  labelledBy,
  describedBy,
  onClose,
  children,
  testId,
}: ModalProps): ReactElement {
  const dialogRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    const active = document.activeElement;
    if (active instanceof HTMLElement && dialog?.contains(active) !== true) {
      previouslyFocused.current = active;
    }
    dialog?.focus();

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.body.style.overflow = previousOverflow;
      previouslyFocused.current?.focus();
    };
  }, []);

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      const dialog = dialogRef.current;
      if (dialog === null) {
        return;
      }
      const focusable = getFocusableElements(dialog);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (first === undefined || last === undefined) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const active = document.activeElement;
      if (event.shiftKey && (active === first || active === dialog)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    },
    [onClose],
  );

  const handleOverlayMouseDown = useCallback(
    (event: MouseEvent<HTMLDivElement>) => {
      // Only a click on the backdrop itself closes; a drag that ends outside the
      // dialog must not dismiss a safety resource.
      if (event.target === event.currentTarget) {
        onClose();
      }
    },
    [onClose],
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4"
      onMouseDown={handleOverlayMouseDown}
      data-testid={testId}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-t-2xl border border-border bg-surface p-6 shadow-soft sm:rounded-2xl"
      >
        {children}
      </div>
    </div>
  );
}
