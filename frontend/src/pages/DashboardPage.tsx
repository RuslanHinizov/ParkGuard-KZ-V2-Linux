/**
 * src/pages/DashboardPage.tsx
 *
 * Overview dashboard — shows live counters from the event-api and
 * a summary of recent violations.  Adım 13 provides the skeleton;
 * later adımlar wire up live camera feeds (Adım 14) and the full
 * violation list (Adım 16).
 */

import { useEffect, useState } from "react";
import { AlertTriangle, Camera, MapPin, Clock } from "lucide-react";
import { violations as violationsApi } from "@/api/client";
import type { Violation } from "@/api/client";
import { formatDistanceToNow } from "date-fns";

// ---------------------------------------------------------------------------
// Stat card
// ---------------------------------------------------------------------------
interface StatCardProps {
  icon: React.ElementType;
  label: string;
  value: string | number;
  sub?: string;
  accent?: string;
}

function StatCard({ icon: Icon, label, value, sub, accent = "brand" }: StatCardProps) {
  return (
    <div className="rounded-xl border border-gray-100 bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm text-gray-500">{label}</p>
          <p className={`mt-1 text-3xl font-bold text-${accent}-600`}>{value}</p>
          {sub && <p className="mt-1 text-xs text-gray-400">{sub}</p>}
        </div>
        <div className={`rounded-lg bg-${accent}-50 p-2`}>
          <Icon className={`h-6 w-6 text-${accent}-500`} />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------
const STATUS_STYLES: Record<string, string> = {
  pending:     "bg-amber-100 text-amber-800",
  approved:    "bg-emerald-100 text-emerald-800",
  disputed:    "bg-red-100 text-red-800",
  card_issued: "bg-indigo-100 text-indigo-800",
};
const STATUS_LABELS: Record<string, string> = {
  pending:     "Күтуде / Ожидание",
  approved:    "Расталды / Подтверждено",
  disputed:    "Дауласты / Оспорено",
  card_issued: "Хабарлама / Уведомление",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[status] ?? "bg-gray-100 text-gray-700"}`}>
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export function DashboardPage() {
  const [recentViolations, setRecentViolations] = useState<Violation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    violationsApi
      .list({ limit: 10 })
      .then((data) => {
        if (!cancelled) {
          setRecentViolations(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err?.message ?? "API error");
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, []);

  const pending  = recentViolations.filter((v) => v.status === "pending").length;
  const approved = recentViolations.filter((v) => v.status === "approved").length;

  return (
    <div className="flex flex-col gap-6 p-6">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">
          Бақылау тақтасы / Дашборд
        </h1>
        <p className="mt-1 text-sm text-gray-500">
          Жылдам шолу / Краткий обзор
        </p>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          icon={AlertTriangle}
          label="Бүгін / Сегодня"
          value={recentViolations.length}
          sub="соңғы 10 / последние 10"
          accent="brand"
        />
        <StatCard
          icon={Clock}
          label="Күтуде / Ожидают"
          value={pending}
          sub="қарастырылмаған / не рассмотрено"
          accent="amber"
        />
        <StatCard
          icon={Camera}
          label="Расталды / Подтверждено"
          value={approved}
          accent="emerald"
        />
        <StatCard
          icon={MapPin}
          label="Жалпы / Всего"
          value={recentViolations.length}
          sub="жүктелген / загружено"
          accent="indigo"
        />
      </div>

      {/* Recent violations table */}
      <div className="rounded-xl border border-gray-100 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
          <h2 className="font-semibold text-gray-900">
            Соңғы бұзушылықтар / Последние нарушения
          </h2>
          <a
            href="/violations"
            className="text-sm text-brand-600 hover:underline"
          >
            Барлығын көру / Показать все →
          </a>
        </div>

        {loading && (
          <div className="flex items-center justify-center py-12 text-gray-400 text-sm">
            Жүктелуде… / Загрузка…
          </div>
        )}

        {error && (
          <div className="flex items-center justify-center py-12 text-red-500 text-sm">
            {error}
          </div>
        )}

        {!loading && !error && recentViolations.length === 0 && (
          <div className="flex items-center justify-center py-12 text-gray-400 text-sm">
            Бұзушылықтар жоқ / Нарушений нет
          </div>
        )}

        {!loading && !error && recentViolations.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-50 bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                  <th className="px-6 py-3">ID</th>
                  <th className="px-6 py-3">Камера</th>
                  <th className="px-6 py-3">Нөмір / Номер</th>
                  <th className="px-6 py-3">Уақыт / Время</th>
                  <th className="px-6 py-3">Күй / Статус</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {recentViolations.map((v) => (
                  <tr key={v.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-6 py-3 font-mono text-gray-600">#{v.id}</td>
                    <td className="px-6 py-3 text-gray-700">{v.camera_id}</td>
                    <td className="px-6 py-3 font-medium">
                      {v.plate_text ?? (
                        <span className="text-gray-400 italic">белгісіз / неизвестно</span>
                      )}
                    </td>
                    <td className="px-6 py-3 text-gray-500">
                      {formatDistanceToNow(new Date(v.violation_time), {
                        addSuffix: true,
                      })}
                    </td>
                    <td className="px-6 py-3">
                      <StatusBadge status={v.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
