/**
 * src/pages/PlaceholderPage.tsx
 *
 * Generic placeholder for pages not yet implemented (Cameras, Zones,
 * Violations detail, Settings). Replaced by actual page components in
 * later adımlar.
 */

interface PlaceholderPageProps {
  title: string;
  subtitle?: string;
}

export function PlaceholderPage({ title, subtitle }: PlaceholderPageProps) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 p-12 text-center">
      <div className="rounded-full bg-gray-100 p-5">
        <span className="text-4xl">🚧</span>
      </div>
      <h2 className="text-xl font-semibold text-gray-700">{title}</h2>
      {subtitle && <p className="max-w-sm text-sm text-gray-400">{subtitle}</p>}
      <p className="text-xs text-gray-300">Келесі адымда / В следующем адыме</p>
    </div>
  );
}
