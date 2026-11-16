// Display-only formatting. These never compute business values: they turn a
// number the backend already computed into text.

/** Backend money is integer cents. Show it as currency text. */
export function formatCents(cents: number, currency?: string | null): string {
  const units = cents / 100; // unit conversion for display only
  if (currency) {
    try {
      return new Intl.NumberFormat('en-CA', { style: 'currency', currency }).format(units);
    } catch {
      // unknown currency code: fall through to a plain number with the code
    }
  }
  const plain = new Intl.NumberFormat('en-CA', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(units);
  return currency ? `${plain} ${currency}` : plain;
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString('en-CA', { dateStyle: 'medium', timeStyle: 'short' });
}

/** The first characters of a hash, enough to compare by eye. */
export function shortHash(hash: string): string {
  return hash.length > 12 ? `${hash.slice(0, 12)}…` : hash;
}

export function humanize(value: string): string {
  return value.replace(/_/g, ' ');
}
