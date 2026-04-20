/**
 * src/stores/operatorStore.ts
 *
 * Zustand store for the operator identity.
 *
 * The dashboard requires no login (spec §11 / §17). On first visit the
 * OperatorNameModal prompts for a name and stores it in localStorage.
 * Every API mutation sends the name as the `X-Operator-Name` header so
 * audit_log rows carry operator provenance.
 *
 * The name is validated to be non-empty and ≤ 64 chars (matching the
 * DB column length).
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

const STORAGE_KEY = "parkguard_operator_name";
const MAX_LENGTH = 64;

interface OperatorState {
  /** Operator name — null means the modal hasn't been dismissed yet. */
  operatorName: string | null;
  /** True while the modal is visible (first visit or manual re-open). */
  modalOpen: boolean;

  setOperatorName: (name: string) => void;
  openModal: () => void;
  closeModal: () => void;
}

export const useOperatorStore = create<OperatorState>()(
  persist(
    (set) => ({
      operatorName: null,
      modalOpen: false,

      setOperatorName: (name: string) => {
        const trimmed = name.trim().slice(0, MAX_LENGTH);
        if (!trimmed) return;
        set({ operatorName: trimmed, modalOpen: false });
      },

      openModal: () => set({ modalOpen: true }),
      closeModal: () => set({ modalOpen: false }),
    }),
    {
      name: STORAGE_KEY,
      // Only persist the name — do not persist modal open state.
      partialize: (state) => ({ operatorName: state.operatorName }),
    }
  )
);

/** Returns the operator name or "Unknown" when not yet set. */
export function getOperatorHeader(): string {
  return useOperatorStore.getState().operatorName ?? "Unknown";
}
