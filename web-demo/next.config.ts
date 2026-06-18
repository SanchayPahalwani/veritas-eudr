import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // MapLibre's worker/WebGL lifecycle does not survive React StrictMode's dev-only
  // double-mount (create → remove → create races the worker pool). Disable it; the
  // production static export never double-mounts, so this only affects dev.
  reactStrictMode: false,
  // Pure static export: the demo ships as a folder of files (no Node server, no
  // database, no network at runtime). All engine output is pre-baked JSON under
  // public/data — see scripts/export_demo_data.py in the repo root.
  output: "export",
  images: { unoptimized: true },
  // Trailing slashes keep the static `out/` directory portable across hosts.
  trailingSlash: true,
};

export default nextConfig;
