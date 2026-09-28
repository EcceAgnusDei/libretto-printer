import type { NextConfig } from "next";
import { loadEnvConfig } from "@next/env";
import { resolve } from "path";

// Load the single monorepo .env at the repo root (parent of web/).
loadEnvConfig(resolve(__dirname, ".."));

const nextConfig: NextConfig = {};

export default nextConfig;
