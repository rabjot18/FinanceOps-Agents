import { createContext, useContext } from 'react';

export interface Toast {
  id: number;
  title: string;
  body?: string;
  to?: string;
  tone?: 'info' | 'success' | 'attention';
}

export type PushToast = (toast: Omit<Toast, 'id'>) => void;

export const ToastContext = createContext<PushToast>(() => {});

export function useToast(): PushToast {
  return useContext(ToastContext);
}
