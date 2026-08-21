import { NavLink } from "react-router-dom";

const NAV_ITEMS = [
  { to: "/", label: "Market", icon: "show_chart" },
  { to: "/analysis", label: "Analysis", icon: "analytics" },
  { to: "/regulatory", label: "Regulatory", icon: "gavel" },
  { to: "/system", label: "System", icon: "settings_heart" },
];

function NavItem({ to, label, icon }) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      className={({ isActive }) =>
        `flex items-center gap-3 p-3 rounded-lg transition-all duration-150 ease-in-out ${
          isActive
            ? "bg-secondary-container text-on-secondary-container font-semibold"
            : "text-on-surface-variant hover:bg-surface-container-high"
        }`
      }
    >
      <span className="material-symbols-outlined text-[20px]">{icon}</span>
      <span className="text-label-mono font-label-mono uppercase tracking-wider">{label}</span>
    </NavLink>
  );
}

export default function Sidebar() {
  return (
    <aside className="hidden md:flex flex-col h-screen p-gutter gap-unit sticky left-0 top-0 bg-surface-container-low border-r border-outline-variant w-64 flex-shrink-0 z-40">
      <div className="mb-8 flex items-center gap-3 px-1">
        <div className="w-10 h-10 rounded bg-primary text-on-primary flex items-center justify-center">
          <span className="material-symbols-outlined">analytics</span>
        </div>
        <div>
          <h1 className="text-headline-sm text-on-surface-variant m-0 p-0 leading-tight">AlphaIntel</h1>
          <p className="text-body-sm text-on-surface-variant opacity-80 m-0 p-0">Terminal v1</p>
        </div>
      </div>
      <nav className="flex-1 space-y-2">
        {NAV_ITEMS.map((item) => (
          <NavItem key={item.to} {...item} />
        ))}
      </nav>
    </aside>
  );
}
