import type { ReactNode } from "react";
import { source } from "@/lib/source";
import { DocsSidebar } from "@/components/DocsSidebar";

export default function DocsLayout({ children }: { children: ReactNode }) {
  return (
    <div className="d-layout">
      <DocsSidebar tree={source.pageTree} />
      <div className="d-main">{children}</div>
    </div>
  );
}
