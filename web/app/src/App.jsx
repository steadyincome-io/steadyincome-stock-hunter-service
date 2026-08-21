import { HashRouter, Routes, Route } from "react-router-dom";
import MarketOverview from "./pages/MarketOverview";
import SectorRegulatory from "./pages/SectorRegulatory";
import TickerAnalysis from "./pages/TickerAnalysis";
import System from "./pages/System";

export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/" element={<MarketOverview />} />
        <Route path="/regulatory" element={<SectorRegulatory />} />
        <Route path="/analysis" element={<TickerAnalysis />} />
        <Route path="/analysis/:ticker" element={<TickerAnalysis />} />
        <Route path="/system" element={<System />} />
      </Routes>
    </HashRouter>
  );
}
