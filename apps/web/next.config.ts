import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

const nextConfig: NextConfig = {
  // Self-contained server bundle for the Docker image (infra/docker/web.Dockerfile).
  output: "standalone",
};

export default withNextIntl(nextConfig);
