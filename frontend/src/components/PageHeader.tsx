import type { ReactElement } from "react";

export interface PageHeaderProps {
  title: string;
  lede: string;
}

/** The heading block every page starts with. */
export function PageHeader({ title, lede }: PageHeaderProps): ReactElement {
  return (
    <div className="rounded-2xl border border-border bg-surface p-6 shadow-soft sm:p-8">
      <h1 className="text-2xl sm:text-3xl">{title}</h1>
      <p className="mt-3 text-lg text-ink-muted">{lede}</p>
    </div>
  );
}
