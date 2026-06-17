import type { ReactNode } from "react";

export function Modal({
  children,
  wide,
  onClose
}: {
  children: ReactNode;
  wide?: boolean;
  onClose: () => void;
}) {
  return (
    <div className="scrim" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`}>{children}</div>
    </div>
  );
}
