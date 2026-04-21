/**
 * src/pages/ViolationsPage.tsx
 *
 * Violations list — Adım 16.
 *
 * Features:
 *   • Filter bar: camera, status, date range, plate text (client-side)
 *   • Expandable row detail: snapshots, full fields, status actions, PDF
 *   • KZ region stats sidebar: group loaded violations by plate_region_code
 *   • Status patch  → PATCH /api/v1/violations/{id}
 *   • PDF trigger   → POST /api/v1/violations/{id}/penalty-card
 *   • WebSocket     → ws://…/ws/violations (auto-appends new rows on push)
 *   • Pagination (offset-based, 50/100/200 per page)
 *
 * All strings are bilingual KK / RU per spec §17.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Download,
  ExternalLink,
  FileText,
  RefreshCw,
  Search,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";
import { format, formatDistanceToNow, parseISO } from "date-fns";
import {
  cameras as camerasApi,
  violations as violationsApi,
} from "@/api/client";
import type { Camera, Violation, ViolationFilters, ViolationStatus } from "@/api/client";

// --------------------------------------------------------------------------- //
// Constants
// --------------------------------------------------------------------------- //

const STATUS_LABEL: Record<ViolationStatus, string> = {
  pending:     "Күтуде / Ожидание",
  approved:    "Расталды / Подтверждено",
  disputed:    "Дауласты / Оспорено",
  card_issued: "Хабарлама / Уведомление",
};

const STATUS_COLOR: Record<ViolationStatus, string> = {
  pending:     "bg-amber-100 text-amber-800",
  approved:    "bg-emerald-100 text-emerald-800",
  disputed:    "bg-red-100 text-red-800",
  card_issued: "bg-indigo-100 text-indigo-800",
};

const ALL_STATUSES: ViolationStatus[] = ["pending", "approved", "disputed", "card_issued"];

/** KZ oblast codes (Nomeroff-net output uses 2-digit numeric codes). */
const KZ_REGION: Record<string, string> = {
  "01": "Алматы қ.",
  "02": "Астана қ.",
  "03": "Алматы обл.",
  "04": "Ақтөбе обл.",
  "05": "Алматы обл. (ескі)",
  "06": "Атырау обл.",
  "07": "Шығыс Қазақстан",
  "08": "Жамбыл обл.",
  "09": "Батыс Қазақстан",
  "10": "Қарағанды обл.",
  "11": "Қостанай обл.",
  "12": "Қызылорда обл.",
  "13": "Маңғыстау обл.",
  "14": "Павлодар обл.",
  "15": "Солтүстік Қазақстан",
  "16": "Оңтүстік Қазақстан",
  "17": "Шымкент қ.",
};

function regionLabel(code: string | null | undefined): string {
  if (!code) return "—";
  return KZ_REGION[code] ?? code;
}

// --------------------------------------------------------------------------- //
// Small helpers
// --------------------------------------------------------------------------- //

function StatusBadge({ status }: { status: ViolationStatus }) {
  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_COLOR[status]}`}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}

function fmtTime(iso: string): string {
  try {
    return format(parseISO(iso), "dd.MM.yyyy HH:mm");
  } catch {
    return iso;
  }
}

function fmtRelative(iso: string): string {
  try {
    return formatDistanceToNow(parseISO(iso), { addSuffix: true });
  } catch {
    return iso;
  }
}

// ISO datetime-local string (for <input type="datetime-local">) → ISO 8601
function localToISO(localVal: string): string {
  if (!localVal) return "";
  return new Date(localVal).toISOString();
}

// --------------------------------------------------------------------------- //
// Region stats panel
// --------------------------------------------------------------------------- //

interface RegionStatsProps {
  violations: Violation[];
  onFilter: (code: string | null) => void;
  activeCode: string | null;
}

function RegionStats({ violations, onFilter, activeCode }: RegionStatsProps) {
  const stats = useMemo(() => {
    const map: Record<string, number> = {};
    for (const v of violations) {
      const code = v.plate_region_code ?? "?";
      map[code] = (map[code] ?? 0) + 1;
    }
    return Object.entries(map).sort((a, b) => b[1] - a[1]);
  }, [violations]);

  if (stats.length === 0) return null;

  return (
    <div className="flex w-52 shrink-0 flex-col gap-2 border-r border-gray-100 bg-white p-4">
      <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
        Аймақтар / Регионы
      </p>
      {activeCode !== null && (
        <button
          onClick={() => onFilter(null)}
          className="flex items-center gap-1 text-xs text-brand-600 hover:underline"
        >
          <X className="h-3 w-3" /> Барлығы / Все
        </button>
      )}
      <div className="flex flex-col gap-1">
        {stats.map(([code, count]) => (
          <button
            key={code}
            onClick={() => onFilter(activeCode === code ? null : code)}
            className={`flex items-center justify-between rounded-md px-2 py-1.5 text-xs transition-colors ${
              activeCode === code
                ? "bg-brand-50 text-brand-700 font-semibold"
                : "text-gray-600 hover:bg-gray-50"
            }`}
          >
            <span className="truncate">
              {code === "?" ? "Белгісіз / Неизвестно" : regionLabel(code)}
              {code !== "?" && (
                <span className="ml-1 text-gray-400">({code})</span>
              )}
            </span>
            <span className="ml-2 shrink-0 rounded-full bg-gray-100 px-1.5 py-0.5 font-medium text-gray-700">
              {count}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Violation detail (expanded row)
// --------------------------------------------------------------------------- //

interface DetailPanelProps {
  violation: Violation;
  onStatusChange: (v: Violation) => void;
  onClose: () => void;
}

function DetailPanel({ violation: v, onStatusChange, onClose }: DetailPanelProps) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const patch = async (newStatus: ViolationStatus) => {
    setBusy(true);
    setMsg(null);
    try {
      const updated = await violationsApi.patch(v.id, { status: newStatus });
      onStatusChange(updated);
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : "Қате / Ошибка");
    } finally {
      setBusy(false);
    }
  };

  const triggerPdf = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const res = await violationsApi.triggerPenaltyCard(v.id);
      if (res.url) {
        setMsg(`PDF: ${res.url}`);
      } else {
        setMsg("PDF жолданды / PDF отправлен в очередь");
      }
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : "Қате / Ошибка");
    } finally {
      setBusy(false);
    }
  };

  return (
    <tr>
      <td colSpan={7} className="bg-gray-50 px-6 py-4">
        <div className="flex flex-wrap gap-6">
          {/* Snapshot images */}
          <div className="flex gap-3">
            {v.snapshot_vehicle_url && (
              <div className="text-center">
                <p className="mb-1 text-xs text-gray-400">Көлік / ТС</p>
                <img
                  src={v.snapshot_vehicle_url}
                  alt="vehicle"
                  className="h-28 w-44 rounded border border-gray-200 object-cover"
                  onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                />
              </div>
            )}
            {v.snapshot_plate_url && (
              <div className="text-center">
                <p className="mb-1 text-xs text-gray-400">Нөмір / Номер</p>
                <img
                  src={v.snapshot_plate_url}
                  alt="plate"
                  className="h-28 w-44 rounded border border-gray-200 object-cover"
                  onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                />
              </div>
            )}
            {!v.snapshot_vehicle_url && !v.snapshot_plate_url && (
              <p className="text-xs text-gray-400 italic">
                Скриншот жоқ / Снимков нет
              </p>
            )}
          </div>

          {/* Detail fields */}
          <div className="flex flex-1 flex-wrap gap-x-8 gap-y-2 text-xs text-gray-600">
            {[
              ["ID",                       `#${v.id}`],
              ["Камера",                   v.camera_id],
              ["Аймақ ID / Зона ID",       String(v.zone_id)],
              ["Нөмір / Номер",            v.plate_text ?? "—"],
              ["Формат / Формат",          v.plate_format ?? "—"],
              ["Аймақ / Регион",           regionLabel(v.plate_region_code)],
              ["Сенімділік / Уверенность", v.plate_confidence != null ? `${(v.plate_confidence * 100).toFixed(1)}%` : "—"],
              ["Көлік түрі / Тип ТС",      v.vehicle_class ?? "—"],
              ["Кірген / Въехал",          fmtTime(v.first_seen_in_zone)],
              ["Бұзушылық / Нарушение",    fmtTime(v.violation_time)],
              ["Шыққан / Выехал",          v.exit_confirmed_time ? fmtTime(v.exit_confirmed_time) : "—"],
              ["Ұзақтық / Длительность",   v.duration_seconds != null ? `${v.duration_seconds}с` : "—"],
              ["Тексерген / Проверил",     v.reviewed_by ?? "—"],
              ["Тексеру уақыты / Время",   v.reviewed_at ? fmtTime(v.reviewed_at) : "—"],
            ].map(([label, val]) => (
              <div key={label}>
                <span className="font-medium text-gray-400">{label}: </span>
                <span>{val}</span>
              </div>
            ))}
          </div>

          {/* Actions */}
          <div className="flex flex-col gap-2">
            <p className="text-xs font-semibold text-gray-500">Әрекеттер / Действия</p>

            {v.status === "pending" && (
              <div className="flex gap-2">
                <button
                  onClick={() => void patch("approved")}
                  disabled={busy}
                  className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50 active:scale-95 transition-all"
                >
                  ✓ Растау / Подтвердить
                </button>
                <button
                  onClick={() => void patch("disputed")}
                  disabled={busy}
                  className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50 active:scale-95 transition-all"
                >
                  ✗ Даулау / Оспорить
                </button>
              </div>
            )}

            {v.status === "disputed" && (
              <button
                onClick={() => void patch("pending")}
                disabled={busy}
                className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-700 hover:bg-amber-100 disabled:opacity-50 active:scale-95 transition-all"
              >
                ↩ Қайтару / Вернуть в ожидание
              </button>
            )}

            {v.status === "approved" && !v.penalty_card_url && (
              <button
                onClick={() => void triggerPdf()}
                disabled={busy}
                className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50 active:scale-95 transition-all"
              >
                {busy ? (
                  <RefreshCw className="h-3 w-3 animate-spin" />
                ) : (
                  <FileText className="h-3 w-3" />
                )}
                PDF жасау / Создать PDF
              </button>
            )}

            {v.penalty_card_url && (
              <a
                href={v.penalty_card_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1.5 rounded-lg bg-indigo-50 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-100 active:scale-95 transition-all"
              >
                <Download className="h-3 w-3" />
                PDF жүктеу / Скачать PDF
                <ExternalLink className="h-3 w-3" />
              </a>
            )}

            {msg && (
              <p className="mt-1 max-w-xs break-all text-xs text-gray-500">{msg}</p>
            )}

            <button
              onClick={onClose}
              className="mt-1 text-xs text-gray-400 hover:text-gray-600 underline"
            >
              Жабу / Закрыть
            </button>
          </div>
        </div>
      </td>
    </tr>
  );
}

// --------------------------------------------------------------------------- //
// Page
// --------------------------------------------------------------------------- //

export function ViolationsPage() {
  // ---- Filter state ----
  const [cameraList, setCameraList] = useState<Camera[]>([]);
  const [filterCameraId, setFilterCameraId] = useState<string>("");
  const [filterStatus, setFilterStatus]     = useState<string>("");
  const [filterFrom, setFilterFrom]         = useState<string>("");
  const [filterTo, setFilterTo]             = useState<string>("");
  const [filterPlate, setFilterPlate]       = useState<string>("");
  const [filterRegion, setFilterRegion]     = useState<string | null>(null);
  const [pageLimit, setPageLimit]           = useState<number>(50);
  const [pageOffset, setPageOffset]         = useState<number>(0);

  // ---- Data state ----
  const [rows, setRows]         = useState<Violation[]>([]);
  const [loading, setLoading]   = useState(true);
  const [loadErr, setLoadErr]   = useState<string | null>(null);
  const [hasMore, setHasMore]   = useState(false);

  // ---- Expanded row ----
  const [expandedId, setExpandedId] = useState<number | null>(null);

  // ---- WebSocket state ----
  const [wsState, setWsState] = useState<"connected" | "disconnected">("disconnected");
  const wsRef = useRef<WebSocket | null>(null);

  // ---- Load cameras ----
  useEffect(() => {
    camerasApi.list().then(setCameraList).catch(() => {});
  }, []);

  // ---- Fetch violations ----
  const fetchViolations = useCallback(
    async (opts?: { reset?: boolean }) => {
      setLoading(true);
      setLoadErr(null);
      const offset = opts?.reset ? 0 : pageOffset;
      if (opts?.reset) setPageOffset(0);

      const filters: ViolationFilters = {
        limit: pageLimit,
        offset,
      };
      if (filterCameraId) filters.camera_id = filterCameraId;
      if (filterStatus)   filters.status    = filterStatus as ViolationStatus;
      if (filterFrom)     filters.from_time = localToISO(filterFrom);
      if (filterTo)       filters.to_time   = localToISO(filterTo);

      try {
        const data = await violationsApi.list(filters);
        setRows(data);
        setHasMore(data.length === pageLimit);
      } catch (err: unknown) {
        setLoadErr(err instanceof Error ? err.message : "API қатесі / Ошибка API");
      } finally {
        setLoading(false);
      }
    },
    [filterCameraId, filterStatus, filterFrom, filterTo, pageLimit, pageOffset],
  );

  // Re-fetch when API filters change (plate is client-side; region is client-side).
  useEffect(() => {
    void fetchViolations({ reset: true });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterCameraId, filterStatus, filterFrom, filterTo, pageLimit]);

  // Re-fetch when offset changes (pagination buttons call setPageOffset).
  useEffect(() => {
    void fetchViolations();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pageOffset]);

  // ---- WebSocket for live push ----
  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const wsUrl = `${proto}://${window.location.host}/ws/violations`;
    let ws: WebSocket;
    let reconnectTimer: ReturnType<typeof setTimeout>;

    const connect = () => {
      ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => setWsState("connected");

      ws.onmessage = (evt) => {
        try {
          const frame = JSON.parse(evt.data as string) as { violation?: Violation };
          if (frame.violation) {
            // Prepend only if it matches current filters (keep it simple: always prepend,
            // client-side plate/region filter will hide it if it doesn't match).
            setRows((prev) => [frame.violation!, ...prev.slice(0, pageLimit - 1)]);
          }
        } catch {
          // malformed frame — ignore
        }
      };

      ws.onclose = () => {
        setWsState("disconnected");
        reconnectTimer = setTimeout(connect, 5_000);
      };

      ws.onerror = () => ws.close();
    };

    connect();

    return () => {
      clearTimeout(reconnectTimer);
      wsRef.current?.close();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- Client-side filtering (plate text + region) ----
  const displayed = useMemo(() => {
    let result = rows;
    if (filterPlate.trim()) {
      const q = filterPlate.trim().toLowerCase();
      result = result.filter((v) =>
        (v.plate_text ?? "").toLowerCase().includes(q),
      );
    }
    if (filterRegion !== null) {
      result = result.filter((v) => (v.plate_region_code ?? "?") === filterRegion);
    }
    return result;
  }, [rows, filterPlate, filterRegion]);

  // ---- Update a violation in local state after patch ----
  const updateRow = useCallback((updated: Violation) => {
    setRows((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
  }, []);

  // ---- Pagination ----
  const prevPage = () => setPageOffset(Math.max(0, pageOffset - pageLimit));
  const nextPage = () => setPageOffset(pageOffset + pageLimit);

  // ---- Render ----
  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Page header */}
      <div className="flex shrink-0 flex-wrap items-end gap-3 border-b border-gray-100 bg-white px-6 py-4">
        <div className="mr-auto">
          <h1 className="text-2xl font-bold text-gray-900">
            Бұзушылықтар / Нарушения
          </h1>
          <p className="mt-0.5 text-sm text-gray-500">
            Барлық жазбалар / Все записи
          </p>
        </div>

        {/* WS indicator */}
        <div className="flex items-center gap-1.5 text-xs">
          {wsState === "connected" ? (
            <>
              <Wifi className="h-3.5 w-3.5 text-emerald-500" />
              <span className="text-emerald-600">Live</span>
            </>
          ) : (
            <>
              <WifiOff className="h-3.5 w-3.5 text-gray-400" />
              <span className="text-gray-400">Offline</span>
            </>
          )}
        </div>

        {/* Refresh */}
        <button
          onClick={() => void fetchViolations({ reset: true })}
          className="flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 active:scale-95 transition-all"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Жаңарту / Обновить
        </button>
      </div>

      {/* Filter bar */}
      <div className="flex shrink-0 flex-wrap items-end gap-3 border-b border-gray-100 bg-gray-50 px-6 py-3">
        {/* Camera */}
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-gray-500">Камера</label>
          <div className="relative">
            <select
              value={filterCameraId}
              onChange={(e) => setFilterCameraId(e.target.value)}
              className="appearance-none rounded-lg border border-gray-200 bg-white py-2 pl-3 pr-8 text-sm outline-none focus:border-brand-400 focus:ring-1 focus:ring-brand-400"
            >
              <option value="">Барлығы / Все</option>
              {cameraList.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-2 top-2.5 h-4 w-4 text-gray-400" />
          </div>
        </div>

        {/* Status */}
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-gray-500">Күй / Статус</label>
          <div className="relative">
            <select
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value)}
              className="appearance-none rounded-lg border border-gray-200 bg-white py-2 pl-3 pr-8 text-sm outline-none focus:border-brand-400 focus:ring-1 focus:ring-brand-400"
            >
              <option value="">Барлығы / Все</option>
              {ALL_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABEL[s]}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-2 top-2.5 h-4 w-4 text-gray-400" />
          </div>
        </div>

        {/* Date from */}
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-gray-500">Бастап / С</label>
          <input
            type="datetime-local"
            value={filterFrom}
            onChange={(e) => setFilterFrom(e.target.value)}
            className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-brand-400 focus:ring-1 focus:ring-brand-400"
          />
        </div>

        {/* Date to */}
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-gray-500">Дейін / До</label>
          <input
            type="datetime-local"
            value={filterTo}
            onChange={(e) => setFilterTo(e.target.value)}
            className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-brand-400 focus:ring-1 focus:ring-brand-400"
          />
        </div>

        {/* Plate search */}
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-gray-500">
            Нөмір / Номер
          </label>
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-gray-400" />
            <input
              type="text"
              value={filterPlate}
              onChange={(e) => setFilterPlate(e.target.value)}
              placeholder="ABC123"
              className="rounded-lg border border-gray-200 bg-white py-2 pl-8 pr-3 text-sm outline-none focus:border-brand-400 focus:ring-1 focus:ring-brand-400"
            />
          </div>
        </div>

        {/* Limit */}
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-gray-500">
            Беттегі / На стр.
          </label>
          <div className="relative">
            <select
              value={pageLimit}
              onChange={(e) => setPageLimit(Number(e.target.value))}
              className="appearance-none rounded-lg border border-gray-200 bg-white py-2 pl-3 pr-8 text-sm outline-none focus:border-brand-400 focus:ring-1 focus:ring-brand-400"
            >
              {[50, 100, 200].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-2 top-2.5 h-4 w-4 text-gray-400" />
          </div>
        </div>

        {/* Clear filters */}
        {(filterCameraId || filterStatus || filterFrom || filterTo || filterPlate) && (
          <button
            onClick={() => {
              setFilterCameraId("");
              setFilterStatus("");
              setFilterFrom("");
              setFilterTo("");
              setFilterPlate("");
              setFilterRegion(null);
            }}
            className="flex items-center gap-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-500 hover:bg-gray-50 active:scale-95 transition-all"
          >
            <X className="h-4 w-4" />
            Тазалау / Сбросить
          </button>
        )}
      </div>

      {/* Body: region sidebar + table */}
      <div className="flex flex-1 overflow-hidden">
        {/* Region stats sidebar */}
        {rows.length > 0 && (
          <RegionStats
            violations={rows}
            onFilter={setFilterRegion}
            activeCode={filterRegion}
          />
        )}

        {/* Table area */}
        <div className="flex flex-1 flex-col overflow-auto">
          {/* Loading */}
          {loading && (
            <div className="flex items-center justify-center py-16">
              <RefreshCw className="h-6 w-6 animate-spin text-brand-400" />
            </div>
          )}

          {/* Error */}
          {!loading && loadErr && (
            <div className="flex flex-col items-center justify-center gap-3 py-16">
              <AlertTriangle className="h-8 w-8 text-red-400" />
              <p className="text-sm text-red-600">{loadErr}</p>
              <button
                onClick={() => void fetchViolations({ reset: true })}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm text-white hover:bg-red-700 active:scale-95 transition-all"
              >
                Қайта жүктеу / Повторить
              </button>
            </div>
          )}

          {/* Empty */}
          {!loading && !loadErr && displayed.length === 0 && (
            <div className="flex flex-col items-center justify-center gap-3 py-20 text-center">
              <div className="rounded-full bg-gray-100 p-4">
                <FileText className="h-8 w-8 text-gray-400" />
              </div>
              <p className="text-sm font-medium text-gray-600">
                Бұзушылықтар табылмады / Нарушений не найдено
              </p>
              <p className="text-xs text-gray-400">
                Сүзгіні өзгертіп көріңіз / Попробуйте изменить фильтры
              </p>
            </div>
          )}

          {/* Table */}
          {!loading && !loadErr && displayed.length > 0 && (
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10 border-b border-gray-100 bg-white">
                <tr className="text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                  <th className="w-8 px-4 py-3" />
                  <th className="px-4 py-3">ID</th>
                  <th className="px-4 py-3">Камера</th>
                  <th className="px-4 py-3">Нөмір / Номер</th>
                  <th className="px-4 py-3">Аймақ / Регион</th>
                  <th className="px-4 py-3">Уақыт / Время</th>
                  <th className="px-4 py-3">Күй / Статус</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {displayed.map((v) => (
                  <>
                    <tr
                      key={v.id}
                      onClick={() =>
                        setExpandedId((prev) => (prev === v.id ? null : v.id))
                      }
                      className="cursor-pointer hover:bg-gray-50 transition-colors"
                    >
                      <td className="px-4 py-3 text-gray-400">
                        {expandedId === v.id ? (
                          <ChevronDown className="h-4 w-4" />
                        ) : (
                          <ChevronRight className="h-4 w-4" />
                        )}
                      </td>
                      <td className="px-4 py-3 font-mono text-gray-600">
                        #{v.id}
                      </td>
                      <td className="px-4 py-3 text-gray-700">
                        {v.camera_id}
                      </td>
                      <td className="px-4 py-3 font-medium">
                        {v.plate_text ?? (
                          <span className="text-gray-400 italic">
                            белгісіз / неизвестно
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-gray-500">
                        {regionLabel(v.plate_region_code)}
                        {v.plate_region_code && (
                          <span className="ml-1 text-gray-400">
                            ({v.plate_region_code})
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-gray-500">
                        <span title={fmtTime(v.violation_time)}>
                          {fmtRelative(v.violation_time)}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <StatusBadge status={v.status} />
                      </td>
                    </tr>

                    {expandedId === v.id && (
                      <DetailPanel
                        key={`detail-${v.id}`}
                        violation={v}
                        onStatusChange={(updated) => {
                          updateRow(updated);
                        }}
                        onClose={() => setExpandedId(null)}
                      />
                    )}
                  </>
                ))}
              </tbody>
            </table>
          )}

          {/* Pagination */}
          {!loading && !loadErr && (rows.length > 0 || pageOffset > 0) && (
            <div className="flex shrink-0 items-center justify-between border-t border-gray-100 bg-white px-6 py-3">
              <p className="text-xs text-gray-500">
                {pageOffset + 1}–{pageOffset + displayed.length} жазба /
                запись{displayed.length !== rows.length && ` (${displayed.length} сүзгіден / из ${rows.length})`}
              </p>
              <div className="flex gap-2">
                <button
                  onClick={prevPage}
                  disabled={pageOffset === 0}
                  className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-40 active:scale-95 transition-all"
                >
                  ← Алдыңғы / Назад
                </button>
                <button
                  onClick={nextPage}
                  disabled={!hasMore}
                  className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-40 active:scale-95 transition-all"
                >
                  Келесі / Вперёд →
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
