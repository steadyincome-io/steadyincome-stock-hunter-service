import Sidebar from "./Sidebar";
import Header from "./Header";

export default function Layout({ title, children }) {
  return (
    <div className="bg-background text-on-background flex h-screen overflow-hidden">
      <Sidebar />
      <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <Header title={title} />
        <div className="flex-1 overflow-auto p-container-margin bg-background">
          <div className="max-w-[1440px] mx-auto space-y-6 pb-8">{children}</div>
        </div>
      </main>
    </div>
  );
}
