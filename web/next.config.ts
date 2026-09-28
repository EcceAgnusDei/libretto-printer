import type { NextConfig } from "next";
import { loadEnvConfig } from "@next/env";
import { resolve } from "path";

loadEnvConfig(resolve(__dirname, ".."));

const nextConfig: NextConfig = {};

export default nextConfig;
