/**
 * src/pages/CamerasPage.tsx
 *
 * Live camera grid — Adım 14.
 *
 * Each enabled camera is displayed as an MJPEG <img> card.  The browser
 * natively handles multipart/x-mixed-replace streams; we just point the
 * `src` at the proxy endpoint `/api/v1/cameras/{id}/stream`.
 *
 * States handled per-tile:
 *   • loading  — img not yet emitted a frame (onLoad not fired yet)
 *   • error    — img failed to load (503 = no mjpeg_url, 502 = unreachable)
 *   • live     — streaming normally
 *
 * The page itself shows a spinner while the cameras list is being fetched,
 * and an empty-state message when no enabled cameras exist.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Camera as CameraIcon, RefreshCw, VideoOff, Wifi } from "lucide-react";
import { cameras as camerasApi } from "@/api/client";
import type { Camera } from "@/api/client";

// --------------------------------------------------------------------------- //
// Camera tile
// --------------------------------------------------------------------------- //

type TileState = "loading" | "live" | "error";

interface CameraTileProps {
  camera: Camera;
}

function CameraTile({ camera }: CameraTileProps) {
  const [state, setState] = useState<TileState>("loading");
  // Append a cache-bust key so a manual refresh forces a new HTTP request.
  const [key, setKey] = useState(0);
  const imgRef = useRef<HTMLImageElement>(null);

  const streamUrl = `/api/v1/cameras/${camera.id}/stream?_k=${key}`;

  const handleLoad = useCallback(() => setState("live"), []);
  const handleError = useCallback(() => setState("error"), []);

  const handleRefresh = () => {
    setState("loading");
    setKey((k) => k + 1);
  };

  return (
    <div className="group relative overflow-hidden rounded-xl border border-gray-200 bg-black shadow-sm">
      {/* Stream image — always rendered so the browser keeps the connection */}
      {/* eslint-disable-next-line jsx-a11y/alt-text */}
      <img
        ref={imgRef}
        src={streamUrl}
        key={key}
        onLoad={handleLoad}
        onError={handleError}
        className={`aspect-video w-full object-cover transition-opacity duration-300 ${
          state === "live" ? "opacity-100" : "opacity-0"
        }`}
        style={{ display: "block" }}
      />

      {/* Loading overlay */}
      {state === "loading" && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-gray-900">
          <RefreshCw className="h-8 w-8 animate-spin text-gray-400" />
          <p className="text-xs text-gray-400">Жүктелуде… / Загрузка…</p>
        </div>
      )}

      {/* Error overlay */}
      {state === "error" && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-gray-900 px-4 text-center">
          <VideoOff className="h-10 w-10 text-gray-600" />
          <p className="text-sm font-medium text-gray-300">
            {camera.name}
          </p>
          <p className="text-xs text-gray-500">
            Трансляция қолжетімсіз / Трансляция недоступна
          </p>
          <button
            onClick={handleRefresh}
            className="mt-1 flex items-center gap-1.5 rounded-md bg-gray-700 px-3 py-1.5 text-xs text-gray-200 hover:bg-gray-600 active:scale-95 transition-all"
          >
            <RefreshCw className="h-3 w-3" />
            Қайта жүктеу / Обновить
          </button>
        </div>
      )}

      {/* Info overlay — always visible at bottom */}
      <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent px-3 py-2">
        <div className="flex items-center justify-between">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-white">
              {camera.name}
            </p>
            <p className="truncate text-xs text-gray-400">{camera.id}</p>
          </div>
          <div className="ml-2 flex shrink-0 items-center gap-1">
            {state === "live" ? (
              <span className="flex items-center gap-1 rounded-full bg-emerald-600/80 px-2 py-0.5 text-xs font-medium text-white">
                <Wifi className="h-3 w-3" />
                LIVE
              </span>
            ) : state === "loading" ? (
              <span className="rounded-full bg-amber-600/80 px-2 py-0.5 text-xs text-white">
                …
              </span>
            ) : (
              <span className="rounded-full bg-red-700/80 px-2 py-0.5 text-xs text-white">
                OFFLINE
              </span>
            )}
          </div>
        </div>
        {camera.location_description && (
          <p className="mt-0.5 truncate text-xs text-gray-400">
            📍 {camera.location_description}
          </p>
        )}
      </div>

      {/* Top-right: manual refresh button (visible on hover) */}
      {state === "live" && (
        <button
          onClick={handleRefresh}
          className="absolute right-2 top-2 rounded-md bg-black/50 p-1.5 text-white opacity-0 transition-opacity group-hover:opacity-100 hover:bg-black/70"
          title="Трансляцияны жаңарту / Обновить трансляцию"
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Page
// --------------------------------------------------------------------------- //

type PageState = "loading" | "ready" | "error";

export function CamerasPage() {
  const [cameraList, setCameraList] = useState<Camera[]>([]);
  const [pageState, setPageState] = useState<PageState>("loading");
  const [pageError, setPageError] = useState<string | null>(null);

  const fetchCameras = useCallback(() => {
    setPageState("loading");
    setPageError(null);
    camerasApi
      .list({ enabled: true })
      .then((data) => {
        setCameraList(data);
        setPageState("ready");
      })
      .catch((err: unknown) => {
        const msg =
          err instanceof Error ? err.message : "API қатесі / Ошибка API";
        setPageError(msg);
        setPageState("error");
      });
  }, []);

  useEffect(() => {
    fetchCameras();
  }, [fetchCameras]);

  // Determine responsive grid columns based on camera count.
  const gridClass =
    cameraList.length === 1
      ? "grid-cols-1 max-w-2xl"
      : cameraList.length === 2
        ? "grid-cols-1 sm:grid-cols-2"
        : cameraList.length <= 4
          ? "grid-cols-1 sm:grid-cols-2 lg:grid-cols-2"
          : "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4";

  return (
    <div className="flex flex-col gap-6 p-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">
            Камералар / Камеры
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            Тікелей бейне берілісі / Прямая трансляция (MJPEG)
          </p>
        </div>
        <button
          onClick={fetchCameras}
          className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 active:scale-95 transition-all"
        >
          <RefreshCw className="h-4 w-4" />
          Жаңарту / Обновить
        </button>
      </div>

      {/* Loading */}
      {pageState === "loading" && (
        <div className="flex items-center justify-center py-24">
          <div className="flex flex-col items-center gap-3">
            <RefreshCw className="h-8 w-8 animate-spin text-brand-500" />
            <p className="text-sm text-gray-500">
              Камералар жүктелуде… / Загрузка камер…
            </p>
          </div>
        </div>
      )}

      {/* Error */}
      {pageState === "error" && (
        <div className="flex flex-col items-center justify-center gap-4 rounded-xl border border-red-100 bg-red-50 py-16">
          <CameraIcon className="h-10 w-10 text-red-400" />
          <p className="text-sm font-medium text-red-700">{pageError}</p>
          <button
            onClick={fetchCameras}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm text-white hover:bg-red-700 active:scale-95 transition-all"
          >
            Қайта көру / Повторить
          </button>
        </div>
      )}

      {/* Empty state */}
      {pageState === "ready" && cameraList.length === 0 && (
        <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-gray-100 bg-white py-20 text-center shadow-sm">
          <div className="rounded-full bg-gray-100 p-4">
            <CameraIcon className="h-8 w-8 text-gray-400" />
          </div>
          <p className="text-base font-medium text-gray-700">
            Қосулы камералар жоқ / Нет активных камер
          </p>
          <p className="max-w-xs text-sm text-gray-400">
            Камера қосу үшін API арқылы камера жазбасын жасаңыз және{" "}
            <code className="rounded bg-gray-100 px-1">enabled=true</code>{" "}
            болуы керек.
          </p>
        </div>
      )}

      {/* Camera grid */}
      {pageState === "ready" && cameraList.length > 0 && (
        <>
          <p className="text-xs text-gray-400">
            {cameraList.length}{" "}
            {cameraList.length === 1
              ? "камера белсенді / камера активна"
              : "камера белсенді / камер активно"}
          </p>
          <div className={`grid gap-4 ${gridClass}`}>
            {cameraList.map((cam) => (
              <CameraTile key={cam.id} camera={cam} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
