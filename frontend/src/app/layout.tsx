import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Storage CMP",
  description: "Cloud Management Platform for Huawei OceanStor Dorado V7 and OceanProtect",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
