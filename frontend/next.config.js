/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  experimental: {
    // App Router is stable in Next.js 14, no flag needed
  },
};

module.exports = nextConfig;
