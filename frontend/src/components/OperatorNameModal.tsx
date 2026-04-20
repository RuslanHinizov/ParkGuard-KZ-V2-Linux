/**
 * src/components/OperatorNameModal.tsx
 *
 * Shown on first visit (when no operator name is persisted) and when
 * the user clicks "Change name" in the sidebar.
 *
 * The dashboard does not have authentication — this is an honour-system
 * name capture for audit log provenance only (spec §11 / §17).
 */

import React, { useState, useCallback, useEffect } from "react";
import { UserCircle } from "lucide-react";
import { useOperatorStore } from "@/stores/operatorStore";

export function OperatorNameModal() {
  const { operatorName, modalOpen, setOperatorName, closeModal } =
    useOperatorStore();

  const [value, setValue] = useState(operatorName ?? "");
  const [error, setError] = useState("");

  // Sync local value when modal re-opens.
  useEffect(() => {
    if (modalOpen) {
      setValue(operatorName ?? "");
      setError("");
    }
  }, [modalOpen, operatorName]);

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const trimmed = value.trim();
      if (!trimmed) {
        setError("Атыңызды енгізіңіз / Введите ваше имя");
        return;
      }
      setOperatorName(trimmed);
    },
    [value, setOperatorName]
  );

  if (!modalOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="operator-modal-title"
    >
      <div className="w-full max-w-md rounded-2xl bg-white p-8 shadow-2xl">
        {/* Icon */}
        <div className="mb-4 flex justify-center">
          <div className="rounded-full bg-brand-50 p-4">
            <UserCircle className="h-10 w-10 text-brand-600" />
          </div>
        </div>

        {/* Title */}
        <h2
          id="operator-modal-title"
          className="mb-1 text-center text-2xl font-bold text-gray-900"
        >
          ParkGuard KZ
        </h2>
        <p className="mb-6 text-center text-sm text-gray-500">
          Операторды идентификациялау / Идентификация оператора
        </p>

        {/* Form */}
        <form onSubmit={handleSubmit} noValidate>
          <label
            htmlFor="operator-name"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Атыңыз / Ваше имя
          </label>
          <input
            id="operator-name"
            type="text"
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              if (error) setError("");
            }}
            placeholder="Мысалы: Айгерім Бекова"
            maxLength={64}
            autoFocus
            className={`w-full rounded-lg border px-4 py-2.5 text-gray-900 outline-none transition
              focus:ring-2 focus:ring-brand-500
              ${error ? "border-red-400 focus:ring-red-400" : "border-gray-300"}`}
          />
          {error && (
            <p className="mt-1 text-xs text-red-500" role="alert">
              {error}
            </p>
          )}

          <button
            type="submit"
            className="mt-4 w-full rounded-lg bg-brand-600 px-4 py-2.5 text-sm
                       font-semibold text-white transition hover:bg-brand-700
                       active:scale-95"
          >
            Жалғастыру / Продолжить
          </button>

          {/* Allow closing without saving if a name is already set */}
          {operatorName && (
            <button
              type="button"
              onClick={closeModal}
              className="mt-2 w-full rounded-lg border border-gray-200 px-4 py-2 text-sm
                         text-gray-600 transition hover:bg-gray-50"
            >
              Болдырмау / Отмена
            </button>
          )}
        </form>

        {/* Disclaimer */}
        <p className="mt-4 text-center text-xs text-gray-400">
          Бұл аутентификация емес — тек аудит журналы үшін.
          <br />
          Это не аутентификация — только для журнала аудита.
        </p>
      </div>
    </div>
  );
}
