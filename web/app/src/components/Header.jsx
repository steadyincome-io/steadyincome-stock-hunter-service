import { useNavigate } from "react-router-dom";
import { useState } from "react";

export default function Header({ title }) {
  const [search, setSearch] = useState("");
  const navigate = useNavigate();

  function onSubmit(e) {
    e.preventDefault();
    const ticker = search.trim().toUpperCase();
    if (ticker) navigate(`/analysis/${ticker}`);
  }

  return (
    <header className="bg-surface border-b border-outline-variant flex justify-between items-center h-14 px-container-margin w-full sticky top-0 z-30 shrink-0">
      <h2 className="text-headline-md font-bold text-on-surface m-0">{title}</h2>
      <form onSubmit={onSubmit} className="flex-1 max-w-xl mx-8 hidden sm:block">
        <div className="relative">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-outline text-sm">
            search
          </span>
          <input
            className="w-full bg-surface-container-low border border-outline-variant text-body-md rounded-lg pl-9 pr-4 py-1.5 focus:outline-none focus:border-secondary focus:ring-1 focus:ring-secondary text-on-surface placeholder-outline transition-colors h-9"
            placeholder="Jump to ticker (e.g. AAPL)..."
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </form>
      <div className="flex items-center gap-2 text-primary">
        <button className="p-2 hover:bg-surface-container-high transition-colors rounded-full">
          <span className="material-symbols-outlined">notifications</span>
        </button>
        <button className="p-2 hover:bg-surface-container-high transition-colors rounded-full">
          <span className="material-symbols-outlined">account_circle</span>
        </button>
      </div>
    </header>
  );
}
