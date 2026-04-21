/**
 * src/App.tsx
 *
 * Root component. Wires together:
 *   - OperatorNameModal (shown if no name is persisted)
 *   - Sidebar + main content area
 *   - React Router routes
 *
 * No login gate — per spec §11 / §17 the dashboard is LAN-only and
 * access control is handled at the network level.
 */

import { useEffect } from "react";
import { Routes, Route } from "react-router-dom";
import { Sidebar } from "@/components/Sidebar";
import { OperatorNameModal } from "@/components/OperatorNameModal";
import { DashboardPage } from "@/pages/DashboardPage";
import { CamerasPage } from "@/pages/CamerasPage";
import { ZonesPage } from "@/pages/ZonesPage";
import { PlaceholderPage } from "@/pages/PlaceholderPage";
import { useOperatorStore } from "@/stores/operatorStore";

export default function App() {
  const { operatorName, openModal } = useOperatorStore();

  // On first visit (no persisted name), open the modal automatically.
  useEffect(() => {
    if (!operatorName) {
      openModal();
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      {/* Operator identification modal — rendered above everything */}
      <OperatorNameModal />

      {/* App shell: sidebar + main */}
      <div className="flex h-screen overflow-hidden">
        <Sidebar />
        <main className="flex flex-1 flex-col overflow-y-auto bg-gray-50">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/cameras" element={<CamerasPage />} />
            <Route path="/zones" element={<ZonesPage />} />
            <Route
              path="/violations"
              element={
                <PlaceholderPage
                  title="Бұзушылықтар / Нарушения"
                  subtitle="Тізім + PDF жүктеу — Adım 16"
                />
              }
            />
            <Route
              path="/settings"
              element={
                <PlaceholderPage
                  title="Баптаулар / Настройки"
                  subtitle="Жүйе параметрлері"
                />
              }
            />
            <Route
              path="*"
              element={
                <PlaceholderPage
                  title="404"
                  subtitle="Бет табылмады / Страница не найдена"
                />
              }
            />
          </Routes>
        </main>
      </div>
    </>
  );
}
