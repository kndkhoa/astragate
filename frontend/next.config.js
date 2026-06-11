/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  experimental: {
    // App Router is stable in Next.js 14, no flag needed
  },
  async rewrites() {
    // Use BACKEND_API_URL for proxy destination on server-side, fallback to NEXT_PUBLIC_API_URL or local API
    const backendUrl = process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
    // Ensure we don't proxy to ourselves if NEXT_PUBLIC_API_URL is relative
    const destinationUrl = backendUrl.startsWith('http') ? backendUrl : 'http://localhost:8000';
    return [
      {
        source: '/api/backend/:path*',
        destination: `${destinationUrl}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
