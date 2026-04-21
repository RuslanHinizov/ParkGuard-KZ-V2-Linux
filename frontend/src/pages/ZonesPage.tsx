/**
 * src/pages/ZonesPage.tsx
 *
 * Zone polygon editor — Adım 15.
 *
 * Layout:
 *   Left panel  — camera selector, zone list, zone detail form
 *   Right panel — MJPEG camera frame as background + SVG polygon overlay
 *
 * Drawing flow:
 *   1. Select a camera from the dropdown.
 *   2. Click "Жаңа аймақ / Новая зона" → editor enters "drawing" mode.
 *   3. Click on the canvas to place vertices (min 3).
 *   4. Double-click (or click the ✓ button) to close the polygon.
 *   5. Fill in the zone form and click Save → POST /api/v1/cameras/{id}/zones.
 *
 *   Existing zones are shown as coloured semi-transparent polygons.
 *   Click an existing zone chip in the list → highlight on canvas.
 *   Click "Edit" → pre-fills form for PUT /api/v1/zones/{id}.
 *   Click "Delete" → DELETE /api/v1/zones/{id} with confirmation.
 *
 * Coordinate system:
 *   All stored (x, y) values are CSS-pixel coordinates within the rendered
 *   <img> element (0,0 = top-left of the frame). The SVG overlay sits on top
 *   of the img with the same dimensions, so no extra scaling is needed at
 *   display time.  Operators must redraw zones if they change the camera
 *   resolution and want new pixel-perfect boundaries.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Camera as CameraIcon,
  Check,
  ChevronDown,
  Edit2,
  PlusCircle,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import {
  cameras as camerasApi,
  zones as zonesApi,
} from "@/api/client";
import type { Camera, PolygonPoint, Zone, ZoneCreate, ZoneType } from "@/api/client";

// --------------------------------------------------------------------------- //
// Constants & helpers
// --------------------------------------------------------------------------- //

const ZONE_TYPE_LABELS: Record<ZoneType, string> = {
  no_parking: "Тоқтауға тыйым / Запрет стоянки",
  tow_away:   "Эвакуатор / Эвакуация",
  restricted: "Шектеулі / Ограниченный",
};

const ZONE_TYPE_OPTIONS: ZoneType[] = ["no_parking", "tow_away", "restricted"];

const DEFAULT_COLORS: Record<ZoneType, string> = {
  no_parking: "#FF3B30",
  tow_away:   "#FF9500",
  restricted: "#FFCC00",
};

/** Hex color → rgba string with given alpha. */
function hexAlpha(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

/** Convert point array to SVG polygon `points` attribute value. */
function toSvgPoints(pts: PolygonPoint[]): string {
  return pts.map((p) => `${p.x},${p.y}`).join(" ");
}

/** Compute bounding-rect-relative pointer coords within an element. */
function relativeCoords(
  e: React.MouseEvent<SVGSVGElement>,
  rect: DOMRect,
): PolygonPoint {
  return {
    x: Math.round(e.clientX - rect.left),
    y: Math.round(e.clientY - rect.top),
  };
}

// --------------------------------------------------------------------------- //
// Form defaults
// --------------------------------------------------------------------------- //

interface ZoneFormValues {
  name: string;
  zone_type: ZoneType;
  threshold_seconds: number;
  exit_confirm_seconds: number;
  cooldown_seconds: number;
  color_hex: string;
  enabled: boolean;
}

const DEFAULT_FORM: ZoneFormValues = {
  name: "",
  zone_type: "no_parking",
  threshold_seconds: 20,
  exit_confirm_seconds: 10,
  cooldown_seconds: 10,
  color_hex: DEFAULT_COLORS.no_parking,
  enabled: true,
};

function zoneToForm(z: Zone): ZoneFormValues {
  return {
    name: z.name,
    zone_type: z.zone_type,
    threshold_seconds: z.threshold_seconds,
    exit_confirm_seconds: z.exit_confirm_seconds,
    cooldown_seconds: z.cooldown_seconds,
    color_hex: z.color_hex,
    enabled: z.enabled,
  };
}

// --------------------------------------------------------------------------- //
// Editor mode
// --------------------------------------------------------------------------- //

type EditorMode =
  | { tag: "idle" }
  | { tag: "drawing"; points: PolygonPoint[] }
  | { tag: "confirm_polygon"; points: PolygonPoint[] }  // polygon closed, filling form
  | { tag: "editing"; zone: Zone; points: PolygonPoint[] }; // editing existing

// --------------------------------------------------------------------------- //
// ZonePolygon — rendered zone on canvas
// --------------------------------------------------------------------------- //

interface ZonePolygonProps {
  zone: Zone;
  highlighted: boolean;
  onClick: () => void;
}

function ZonePolygon({ zone, highlighted, onClick }: ZonePolygonProps) {
  const pts = toSvgPoints(zone.polygon);
  return (
    <g onClick={onClick} style={{ cursor: "pointer" }}>
      <polygon
        points={pts}
        fill={hexAlpha(zone.color_hex, highlighted ? 0.45 : 0.25)}
        stroke={zone.color_hex}
        strokeWidth={highlighted ? 2.5 : 1.5}
        strokeDasharray={highlighted ? undefined : "6 3"}
      />
      {/* Zone name label at centroid */}
      {zone.polygon.length > 0 && (() => {
        const cx = zone.polygon.reduce((s, p) => s + p.x, 0) / zone.polygon.length;
        const cy = zone.polygon.reduce((s, p) => s + p.y, 0) / zone.polygon.length;
        return (
          <text
            x={cx}
            y={cy}
            textAnchor="middle"
            dominantBaseline="middle"
            fontSize={13}
            fontWeight="600"
            fill={zone.color_hex}
            stroke="rgba(0,0,0,0.6)"
            strokeWidth={3}
            paintOrder="stroke"
          >
            {zone.name}
          </text>
        );
      })()}
    </g>
  );
}

// --------------------------------------------------------------------------- //
// ZoneForm
// --------------------------------------------------------------------------- //

interface ZoneFormProps {
  values: ZoneFormValues;
  onChange: (v: ZoneFormValues) => void;
  onSave: () => void;
  onCancel: () => void;
  saving: boolean;
  title: string;
}

function ZoneForm({ values, onChange, onSave, onCancel, saving, title }: ZoneFormProps) {
  const set = <K extends keyof ZoneFormValues>(k: K, v: ZoneFormValues[K]) =>
    onChange({ ...values, [k]: v });

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm font-semibold text-gray-700">{title}</p>

      {/* Name */}
      <div>
        <label className="mb-1 block text-xs font-medium text-gray-500">
          Атауы / Название
        </label>
        <input
          type="text"
          value={values.name}
          onChange={(e) => set("name", e.target.value)}
          maxLength={100}
          placeholder="P1 — Кіреберіс / Въезд P1"
          className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-brand-400 focus:ring-1 focus:ring-brand-400"
        />
      </div>

      {/* Zone type */}
      <div>
        <label className="mb-1 block text-xs font-medium text-gray-500">
          Аймақ түрі / Тип зоны
        </label>
        <div className="relative">
          <select
            value={values.zone_type}
            onChange={(e) => {
              const zt = e.target.value as ZoneType;
              set("zone_type", zt);
              // auto-update color only if still on a default
              if (Object.values(DEFAULT_COLORS).includes(values.color_hex)) {
                set("color_hex", DEFAULT_COLORS[zt]);
              }
            }}
            className="w-full appearance-none rounded-lg border border-gray-200 px-3 py-2 pr-8 text-sm outline-none focus:border-brand-400 focus:ring-1 focus:ring-brand-400"
          >
            {ZONE_TYPE_OPTIONS.map((t) => (
              <option key={t} value={t}>
                {ZONE_TYPE_LABELS[t]}
              </option>
            ))}
          </select>
          <ChevronDown className="pointer-events-none absolute right-2.5 top-2.5 h-4 w-4 text-gray-400" />
        </div>
      </div>

      {/* Color */}
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <label className="mb-1 block text-xs font-medium text-gray-500">
            Түсі / Цвет (#RRGGBB)
          </label>
          <input
            type="text"
            value={values.color_hex}
            onChange={(e) => set("color_hex", e.target.value)}
            pattern="^#[0-9A-Fa-f]{6}$"
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-brand-400 focus:ring-1 focus:ring-brand-400"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-500">
            &nbsp;
          </label>
          <input
            type="color"
            value={values.color_hex}
            onChange={(e) => set("color_hex", e.target.value)}
            className="h-[38px] w-10 cursor-pointer rounded border border-gray-200"
          />
        </div>
      </div>

      {/* Thresholds */}
      <div className="grid grid-cols-3 gap-2">
        {(
          [
            ["threshold_seconds",      "Шек. / Порог, с"],
            ["exit_confirm_seconds",   "Шығу / Выезд, с"],
            ["cooldown_seconds",       "Салқын / Откат, с"],
          ] as const
        ).map(([field, label]) => (
          <div key={field}>
            <label className="mb-1 block text-xs font-medium text-gray-500">
              {label}
            </label>
            <input
              type="number"
              min={1}
              max={field === "threshold_seconds" ? 3600 : 300}
              value={values[field]}
              onChange={(e) => set(field, Number(e.target.value))}
              className="w-full rounded-lg border border-gray-200 px-2 py-2 text-sm outline-none focus:border-brand-400 focus:ring-1 focus:ring-brand-400"
            />
          </div>
        ))}
      </div>

      {/* Enabled toggle */}
      <label className="flex cursor-pointer items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={values.enabled}
          onChange={(e) => set("enabled", e.target.checked)}
          className="h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-500"
        />
        <span className="text-gray-600">
          Қосулы / Активна
        </span>
      </label>

      {/* Actions */}
      <div className="flex gap-2 pt-1">
        <button
          onClick={onSave}
          disabled={saving || !values.name.trim()}
          className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50 active:scale-95 transition-all"
        >
          {saving ? (
            <RefreshCw className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Check className="h-3.5 w-3.5" />
          )}
          Сақтау / Сохранить
        </button>
        <button
          onClick={onCancel}
          disabled={saving}
          className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-600 hover:bg-gray-50 active:scale-95 transition-all"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Page
// --------------------------------------------------------------------------- //

export function ZonesPage() {
  // ---- Camera list ----
  const [cameraList, setCameraList] = useState<Camera[]>([]);
  const [selectedCamId, setSelectedCamId] = useState<string | null>(null);

  // ---- Zone list for selected camera ----
  const [zoneList, setZoneList] = useState<Zone[]>([]);
  const [highlightedZoneId, setHighlightedZoneId] = useState<number | null>(null);

  // ---- Editor state ----
  const [mode, setMode] = useState<EditorMode>({ tag: "idle" });
  const [formValues, setFormValues] = useState<ZoneFormValues>(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // ---- Live preview cursor point while drawing ----
  const [cursorPt, setCursorPt] = useState<PolygonPoint | null>(null);

  // ---- Canvas container ref for coordinate mapping ----
  const svgRef = useRef<SVGSVGElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const [imgSize, setImgSize] = useState<{ w: number; h: number }>({ w: 1280, h: 720 });

  // ---- Camera image key for refresh ----
  const [imgKey, setImgKey] = useState(0);

  // Load camera list once
  useEffect(() => {
    camerasApi.list().then((list) => {
      setCameraList(list);
      if (list.length > 0 && !selectedCamId) {
        setSelectedCamId(list[0].id);
      }
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Load zones when camera changes
  const loadZones = useCallback((camId: string) => {
    setZoneList([]);
    setMode({ tag: "idle" });
    zonesApi.listForCamera(camId).then(setZoneList).catch(() => setZoneList([]));
  }, []);

  useEffect(() => {
    if (selectedCamId) loadZones(selectedCamId);
  }, [selectedCamId, loadZones]);

  // Measure rendered img size so SVG viewBox stays correct
  useEffect(() => {
    const el = imgRef.current;
    if (!el) return;
    const obs = new ResizeObserver(() => {
      setImgSize({ w: el.clientWidth, h: el.clientHeight });
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, [selectedCamId, imgKey]);

  // ---- Derived state ----
  const selectedCam = useMemo(
    () => cameraList.find((c) => c.id === selectedCamId) ?? null,
    [cameraList, selectedCamId],
  );

  const isDrawing = mode.tag === "drawing";
  const drawPoints =
    mode.tag === "drawing" ? mode.points :
    mode.tag === "confirm_polygon" ? mode.points :
    mode.tag === "editing" ? mode.points : [];
  const showForm =
    mode.tag === "confirm_polygon" || mode.tag === "editing";

  // ---- SVG mouse handlers ----
  const getSvgRect = () => svgRef.current?.getBoundingClientRect() ?? null;

  const handleSvgClick = (e: React.MouseEvent<SVGSVGElement>) => {
    if (mode.tag !== "drawing") return;
    const rect = getSvgRect();
    if (!rect) return;
    const pt = relativeCoords(e, rect);
    setMode({ tag: "drawing", points: [...mode.points, pt] });
  };

  const handleSvgDblClick = (e: React.MouseEvent<SVGSVGElement>) => {
    e.preventDefault();
    if (mode.tag !== "drawing") return;
    const pts = mode.points;
    if (pts.length < 3) return; // need at least 3 vertices
    // Close polygon — transition to form
    setMode({ tag: "confirm_polygon", points: pts });
    setFormValues({
      ...DEFAULT_FORM,
      zone_type: DEFAULT_FORM.zone_type,
      color_hex: DEFAULT_COLORS[DEFAULT_FORM.zone_type],
    });
    setSaveError(null);
  };

  const handleSvgMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (mode.tag !== "drawing") { setCursorPt(null); return; }
    const rect = getSvgRect();
    if (!rect) return;
    setCursorPt(relativeCoords(e, rect));
  };

  const handleSvgMouseLeave = () => setCursorPt(null);

  // Close polygon with ✓ button (same as double-click)
  const closePolygon = () => {
    if (mode.tag !== "drawing" || mode.points.length < 3) return;
    setMode({ tag: "confirm_polygon", points: mode.points });
    setFormValues({ ...DEFAULT_FORM, color_hex: DEFAULT_COLORS[DEFAULT_FORM.zone_type] });
    setSaveError(null);
  };

  // Undo last point
  const undoPoint = () => {
    if (mode.tag !== "drawing" || mode.points.length === 0) return;
    setMode({ tag: "drawing", points: mode.points.slice(0, -1) });
  };

  // Cancel drawing / editing
  const cancelEditor = () => {
    setMode({ tag: "idle" });
    setCursorPt(null);
    setSaveError(null);
  };

  // Start drawing new zone
  const startDrawing = () => {
    setMode({ tag: "drawing", points: [] });
    setSaveError(null);
    setHighlightedZoneId(null);
  };

  // Start editing existing zone
  const startEditing = (zone: Zone) => {
    setMode({ tag: "editing", zone, points: [...zone.polygon] });
    setFormValues(zoneToForm(zone));
    setSaveError(null);
    setHighlightedZoneId(zone.id);
  };

  // Save (create or update)
  const handleSave = async () => {
    if (!selectedCamId) return;
    const pts = drawPoints;
    if (pts.length < 3) {
      setSaveError("Минимум 3 нүкте / Минимум 3 точки");
      return;
    }
    if (!formValues.name.trim()) {
      setSaveError("Атауы міндетті / Название обязательно");
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      if (mode.tag === "editing") {
        const updated = await zonesApi.update(mode.zone.id, {
          ...formValues,
          polygon: pts,
        });
        setZoneList((prev) =>
          prev.map((z) => (z.id === updated.id ? updated : z)),
        );
        setHighlightedZoneId(updated.id);
      } else {
        const payload: ZoneCreate = { ...formValues, polygon: pts };
        const created = await zonesApi.create(selectedCamId, payload);
        setZoneList((prev) => [...prev, created]);
        setHighlightedZoneId(created.id);
      }
      setMode({ tag: "idle" });
    } catch (err: unknown) {
      const msg =
        err instanceof Error ? err.message : "Сақтау қатесі / Ошибка сохранения";
      setSaveError(msg);
    } finally {
      setSaving(false);
    }
  };

  // Delete zone
  const handleDelete = async (zone: Zone) => {
    if (
      !window.confirm(
        `"${zone.name}" аймағын жойғыңыз келе ме?\nУдалить зону "${zone.name}"?`,
      )
    )
      return;
    try {
      await zonesApi.remove(zone.id);
      setZoneList((prev) => prev.filter((z) => z.id !== zone.id));
      if (mode.tag === "editing" && mode.zone.id === zone.id) cancelEditor();
      if (highlightedZoneId === zone.id) setHighlightedZoneId(null);
    } catch {
      // ignore
    }
  };

  // ---- Preview polygon while drawing ----
  const previewPoints: PolygonPoint[] =
    isDrawing && cursorPt && drawPoints.length > 0
      ? [...drawPoints, cursorPt]
      : drawPoints;

  // ---- Render ----
  const canvasColor =
    mode.tag === "confirm_polygon" || mode.tag === "editing"
      ? formValues.color_hex
      : "#3B82F6";

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Page header */}
      <div className="flex shrink-0 items-center justify-between border-b border-gray-100 bg-white px-6 py-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">
            Аймақтар / Зоны
          </h1>
          <p className="mt-0.5 text-sm text-gray-500">
            Полигон редакторы / Редактор полигонов
          </p>
        </div>

        {/* Camera selector */}
        <div className="relative">
          <select
            value={selectedCamId ?? ""}
            onChange={(e) => setSelectedCamId(e.target.value || null)}
            className="appearance-none rounded-lg border border-gray-200 bg-white py-2 pl-4 pr-9 text-sm font-medium text-gray-700 shadow-sm outline-none focus:border-brand-400 focus:ring-1 focus:ring-brand-400"
          >
            {cameraList.length === 0 && (
              <option value="">— камера жоқ / нет камер —</option>
            )}
            {cameraList.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.id})
              </option>
            ))}
          </select>
          <ChevronDown className="pointer-events-none absolute right-2.5 top-2.5 h-4 w-4 text-gray-400" />
        </div>
      </div>

      {/* Body: left panel + canvas */}
      <div className="flex flex-1 overflow-hidden">

        {/* ---- Left panel ---- */}
        <aside className="flex w-72 shrink-0 flex-col gap-4 overflow-y-auto border-r border-gray-100 bg-white p-4">

          {/* Zone list */}
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-gray-700">
              Аймақтар / Зоны{" "}
              {zoneList.length > 0 && (
                <span className="ml-1 rounded-full bg-gray-100 px-1.5 py-0.5 text-xs text-gray-500">
                  {zoneList.length}
                </span>
              )}
            </p>
            <button
              onClick={startDrawing}
              disabled={!selectedCamId || isDrawing || mode.tag === "confirm_polygon"}
              className="flex items-center gap-1 rounded-lg bg-brand-600 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-brand-700 disabled:opacity-40 active:scale-95 transition-all"
            >
              <PlusCircle className="h-3.5 w-3.5" />
              Жаңа / Новая
            </button>
          </div>

          {zoneList.length === 0 && !showForm && !isDrawing && (
            <p className="text-xs text-gray-400">
              Аймақтар жоқ. «Жаңа» батырмасын басып, полигон сызыңыз.
              <br />
              Нет зон. Нажмите «Новая» и нарисуйте полигон.
            </p>
          )}

          {zoneList.map((zone) => (
            <div
              key={zone.id}
              onClick={() => setHighlightedZoneId(
                highlightedZoneId === zone.id ? null : zone.id,
              )}
              className={`cursor-pointer rounded-lg border p-3 transition-all ${
                highlightedZoneId === zone.id
                  ? "border-brand-200 bg-brand-50"
                  : "border-gray-100 hover:border-gray-200 hover:bg-gray-50"
              }`}
            >
              <div className="flex items-center gap-2">
                <span
                  className="inline-block h-3 w-3 rounded-full shrink-0"
                  style={{ backgroundColor: zone.color_hex }}
                />
                <span className="flex-1 truncate text-sm font-medium text-gray-800">
                  {zone.name}
                </span>
                <button
                  onClick={(e) => { e.stopPropagation(); startEditing(zone); }}
                  className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                  title="Өңдеу / Редактировать"
                >
                  <Edit2 className="h-3.5 w-3.5" />
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); void handleDelete(zone); }}
                  className="rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-500"
                  title="Жою / Удалить"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
              <p className="mt-1 pl-5 text-xs text-gray-400">
                {ZONE_TYPE_LABELS[zone.zone_type]}
              </p>
              <p className="mt-0.5 pl-5 text-xs text-gray-400">
                ≥ {zone.threshold_seconds}с •{" "}
                {zone.enabled ? (
                  <span className="text-emerald-500">Қосулы</span>
                ) : (
                  <span className="text-gray-400">Өшірулі</span>
                )}
              </p>
            </div>
          ))}

          {/* Drawing instructions */}
          {isDrawing && (
            <div className="rounded-lg border border-blue-100 bg-blue-50 p-3 text-xs text-blue-700">
              <p className="font-semibold">Сызу режимі / Режим рисования</p>
              <p className="mt-1">
                • Нүкте қосу: бір рет басу / Добавить точку: один клик
              </p>
              <p>
                • Жабу: қос басу немесе ✓ / Закрыть: двойной клик или ✓
              </p>
              <p className="mt-1 font-medium text-blue-500">
                {drawPoints.length} нүкте / точки
              </p>
              <div className="mt-2 flex gap-1.5">
                <button
                  onClick={closePolygon}
                  disabled={drawPoints.length < 3}
                  className="flex items-center gap-1 rounded bg-blue-600 px-2 py-1 text-xs text-white disabled:opacity-40"
                >
                  <Check className="h-3 w-3" /> ✓
                </button>
                <button
                  onClick={undoPoint}
                  disabled={drawPoints.length === 0}
                  className="rounded border border-blue-200 px-2 py-1 text-xs text-blue-600 disabled:opacity-40"
                >
                  Undo
                </button>
                <button
                  onClick={cancelEditor}
                  className="rounded border border-gray-200 px-2 py-1 text-xs text-gray-600"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            </div>
          )}

          {/* Zone form */}
          {showForm && (
            <div className="rounded-lg border border-gray-100 bg-gray-50 p-3">
              <ZoneForm
                values={formValues}
                onChange={setFormValues}
                onSave={() => void handleSave()}
                onCancel={cancelEditor}
                saving={saving}
                title={
                  mode.tag === "editing"
                    ? "Өңдеу / Редактировать"
                    : "Жаңа аймақ / Новая зона"
                }
              />
              {saveError && (
                <p className="mt-2 text-xs text-red-600">{saveError}</p>
              )}
            </div>
          )}
        </aside>

        {/* ---- Canvas area ---- */}
        <div className="relative flex flex-1 items-start justify-center overflow-auto bg-gray-900 p-4">
          {!selectedCamId ? (
            <div className="flex flex-col items-center justify-center gap-3 py-20 text-center">
              <CameraIcon className="h-12 w-12 text-gray-600" />
              <p className="text-sm text-gray-500">
                Камераны таңдаңыз / Выберите камеру
              </p>
            </div>
          ) : (
            <div
              className="relative"
              style={{ maxWidth: "100%", width: imgSize.w }}
            >
              {/* Camera frame background */}
              <img
                ref={imgRef}
                key={`${selectedCamId}-${imgKey}`}
                src={`/api/v1/cameras/${selectedCamId}/stream`}
                onLoad={() => {
                  if (imgRef.current) {
                    setImgSize({
                      w: imgRef.current.clientWidth,
                      h: imgRef.current.clientHeight,
                    });
                  }
                }}
                onError={() => {}}
                alt={selectedCam?.name ?? selectedCamId}
                className="block w-full rounded-lg"
                style={{ display: "block" }}
              />

              {/* SVG polygon overlay */}
              <svg
                ref={svgRef}
                viewBox={`0 0 ${imgSize.w} ${imgSize.h}`}
                className="absolute inset-0 h-full w-full rounded-lg"
                style={{
                  cursor: isDrawing ? "crosshair" : "default",
                }}
                onClick={handleSvgClick}
                onDoubleClick={handleSvgDblClick}
                onMouseMove={handleSvgMouseMove}
                onMouseLeave={handleSvgMouseLeave}
              >
                {/* Existing zones */}
                {zoneList
                  .filter((z) =>
                    mode.tag === "editing" ? z.id !== mode.zone.id : true,
                  )
                  .map((zone) => (
                    <ZonePolygon
                      key={zone.id}
                      zone={zone}
                      highlighted={highlightedZoneId === zone.id}
                      onClick={() =>
                        setHighlightedZoneId(
                          highlightedZoneId === zone.id ? null : zone.id,
                        )
                      }
                    />
                  ))}

                {/* In-progress / editing polygon */}
                {(isDrawing || showForm) && previewPoints.length >= 2 && (
                  <polygon
                    points={toSvgPoints(previewPoints)}
                    fill={hexAlpha(canvasColor, 0.2)}
                    stroke={canvasColor}
                    strokeWidth={2}
                    strokeDasharray={isDrawing ? "6 3" : undefined}
                  />
                )}
                {/* Vertex dots */}
                {(isDrawing || showForm) &&
                  drawPoints.map((p, i) => (
                    <circle
                      key={i}
                      cx={p.x}
                      cy={p.y}
                      r={5}
                      fill={canvasColor}
                      stroke="white"
                      strokeWidth={1.5}
                    />
                  ))}
                {/* First-point larger circle (close hint) */}
                {isDrawing && drawPoints.length >= 3 && (
                  <circle
                    cx={drawPoints[0].x}
                    cy={drawPoints[0].y}
                    r={9}
                    fill="transparent"
                    stroke={canvasColor}
                    strokeWidth={2}
                    strokeDasharray="4 2"
                  />
                )}
              </svg>

              {/* Refresh stream button */}
              <button
                onClick={() => setImgKey((k) => k + 1)}
                className="absolute right-2 top-2 rounded-md bg-black/50 p-1.5 text-white hover:bg-black/70"
                title="Бейнені жаңарту / Обновить видео"
              >
                <RefreshCw className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
