/**
 * JWT storage and refresh logic.
 * Uses localStorage for both access token and refresh token (Phase 1 simplicity).
 * Access token: short-lived (15 min), Refresh token: long-lived (7 days).
 */

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("access_token");
}

export function setAccessToken(token: string): void {
  localStorage.setItem("access_token", token);
}

export function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("refresh_token");
}

export function setTokens(accessToken: string, refreshToken: string): void {
  localStorage.setItem("access_token", accessToken);
  localStorage.setItem("refresh_token", refreshToken);
}

export function clearTokens(): void {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
}

/**
 * Decode the JWT payload without verifying the signature.
 * Signature verification happens server-side.
 */
export function getDecodedToken(): {
  sub: string;
  role: string;
  exp: number;
} | null {
  const token = getAccessToken();
  if (!token) return null;
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const payload = JSON.parse(atob(parts[1]));
    return payload;
  } catch {
    return null;
  }
}

/**
 * Returns true if the stored access token exists and has not expired.
 */
export function isAuthenticated(): boolean {
  const decoded = getDecodedToken();
  if (!decoded) return false;
  // exp is in seconds; Date.now() is in milliseconds
  return decoded.exp * 1000 > Date.now();
}

/**
 * Returns true if the current user has the 'admin' role.
 */
export function isAdmin(): boolean {
  const decoded = getDecodedToken();
  return decoded?.role === "admin";
}

/**
 * Attempts to refresh the access token using the stored refresh token.
 * Returns true on success, false on failure (clears tokens on failure).
 */
export async function refreshAccessToken(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;

  try {
    const API_URL =
      process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    const response = await fetch(`${API_URL}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });

    if (!response.ok) {
      clearTokens();
      return false;
    }

    const data = await response.json();
    setAccessToken(data.access_token);
    return true;
  } catch {
    clearTokens();
    return false;
  }
}
