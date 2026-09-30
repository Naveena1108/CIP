import React from "react";

export const metadata = {
  title: "AI CRISS — Institutional Crisis Intelligence",
  description: "Cross-Signal Institutional Risk and Early Warning System",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-slate-950 text-slate-100 antialiased">{children}</body>
    </html>
  );
}
