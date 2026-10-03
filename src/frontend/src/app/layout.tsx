import React from "react";
import "./globals.css";

export const metadata = {
  title: "CIP — Crisis Intelligence Platform",
  description: "Cross-Signal Institutional Crisis Intelligence and Early Warning Platform",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-[#F8F7F4] text-[#252124] antialiased">
        {children}
      </body>
    </html>
  );
}
