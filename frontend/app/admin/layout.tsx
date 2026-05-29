"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  LayoutDashboard,
  Users,
  Server,
  Cpu,
  ShieldCheck,
  LogOut,
  Zap,
} from "lucide-react";
import { NavLink } from "@/components/shared/nav-link";
import { isAuthenticated, isAdmin, clearTokens } from "@/lib/auth";

const navItems = [
  { href: "/admin", label: "Overview", icon: LayoutDashboard, exact: true },
  { href: "/admin/customers", label: "Customers", icon: Users },
  { href: "/admin/providers", label: "Providers", icon: Server },
  { href: "/admin/models", label: "Models", icon: Cpu },
  { href: "/admin/guardrails", label: "Guardrails", icon: ShieldCheck },
];

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();

  // Auth guard: redirect to /login if no valid JWT or not admin role
  useEffect(() => {
    if (!isAuthenticated() || !isAdmin()) {
      router.replace("/login");
    }
  }, [router]);

  function handleSignOut() {
    clearTokens();
    router.replace("/login");
  }

  return (
    <div className="flex min-h-screen bg-background">
      {/* Sidebar */}
      <aside className="flex w-64 flex-col border-r bg-card">
        {/* Brand */}
        <div className="flex h-16 items-center gap-2 border-b px-6">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary">
            <Zap className="h-4 w-4 text-primary-foreground" />
          </div>
          <Link href="/admin" className="text-lg font-bold">
            AstraGate
          </Link>
          <span className="ml-auto rounded-full bg-destructive px-2 py-0.5 text-xs font-medium text-destructive-foreground">
            Admin
          </span>
        </div>

        {/* Navigation */}
        <nav className="flex-1 space-y-1 p-4" aria-label="Admin navigation">
          {navItems.map((item) => (
            <NavLink key={item.href} href={item.href} exact={item.exact}>
              <item.icon className="h-4 w-4 shrink-0" aria-hidden="true" />
              {item.label}
            </NavLink>
          ))}
        </nav>

        {/* User menu placeholder */}
        <div className="border-t p-4">
          <button
            onClick={handleSignOut}
            className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
          >
            <LogOut className="h-4 w-4 shrink-0" aria-hidden="true" />
            Sign Out
          </button>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <div className="mx-auto max-w-6xl p-8">{children}</div>
      </main>
    </div>
  );
}
