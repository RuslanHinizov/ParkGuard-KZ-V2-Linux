/**
 * src/components/Sidebar.tsx
 *
 * Left navigation sidebar. Links activate their route via React Router.
 * The operator name badge at the bottom opens the OperatorNameModal for
 * re-identification without a page reload.
 */

import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Camera,
  MapPin,
  AlertTriangle,
  Settings,
  ChevronRight,
} from "lucide-react";
import { useOperatorStore } from "@/stores/operatorStore";

const NAV_ITEMS = [
  { to: "/",           icon: LayoutDashboard, label: "Бақылау / Дашборд" },
  { to: "/cameras",    icon: Camera,          label: "Камералар / Камеры" },
  { to: "/zones",      icon: MapPin,          label: "Аймақтар / Зоны" },
  { to: "/violations", icon: AlertTriangle,   label: "Бұзушылықтар / Нарушения" },
  { to: "/settings",   icon: Settings,        label: "Баптаулар / Настройки" },
] as const;

export function Sidebar() {
  const { operatorName, openModal } = useOperatorStore();

  return (
    <aside className="flex h-full w-64 flex-col border-r border-gray-200 bg-white">
      {/* Logo */}
      <div className="flex h-16 items-center px-6 border-b border-gray-100">
        <span className="text-xl font-extrabold text-brand-700 tracking-tight">
          Park<span className="text-brand-500">Guard</span>
          <span className="ml-1 text-xs font-normal text-gray-400">KZ</span>
        </span>
      </div>

      {/* Nav links */}
      <nav className="flex-1 overflow-y-auto px-3 py-4">
        <ul className="space-y-1">
          {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
            <li key={to}>
              <NavLink
                to={to}
                end={to === "/"}
                className={({ isActive }) =>
                  `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition
                  ${isActive
                    ? "bg-brand-50 text-brand-700"
                    : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
                  }`
                }
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="truncate">{label}</span>
                {to !== "/" && (
                  <ChevronRight className="ml-auto h-3 w-3 text-gray-300" />
                )}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      {/* Operator badge */}
      <div className="border-t border-gray-100 p-4">
        <button
          onClick={openModal}
          className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left
                     text-sm transition hover:bg-gray-50"
          title="Атты өзгерту / Изменить имя"
        >
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full
                          bg-brand-100 text-brand-700 font-bold text-sm">
            {(operatorName ?? "?")[0].toUpperCase()}
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-gray-900">
              {operatorName ?? "Белгісіз / Неизвестно"}
            </p>
            <p className="text-xs text-gray-400">Оператор</p>
          </div>
        </button>
      </div>
    </aside>
  );
}
