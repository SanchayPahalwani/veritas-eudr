import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jbmono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "VERITAS · EUDR — deforestation due-diligence engine",
  description:
    "A forensic walkthrough of an EU Deforestation Regulation compliance engine: " +
    "geodesic area, convergence-of-evidence risk, an append-only evidence ledger, " +
    "and a Due Diligence Statement that is honest about what it cannot prove.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${jetbrainsMono.variable} antialiased`}
    >
      <body className="grain relative min-h-dvh">{children}</body>
    </html>
  );
}
