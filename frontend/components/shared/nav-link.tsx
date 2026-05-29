"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { clsx } from "clsx";

interface NavLinkProps {
  href: string;
  children: React.ReactNode;
  /** If true, only mark active when the path matches exactly */
  exact?: boolean;
  className?: string;
  activeClassName?: string;
}

/**
 * A navigation link that applies an active style when the current
 * pathname matches the href.
 */
export function NavLink({
  href,
  children,
  exact = false,
  className,
  activeClassName,
}: NavLinkProps) {
  const pathname = usePathname();
  const isActive = exact ? pathname === href : pathname.startsWith(href);

  return (
    <Link
      href={href}
      className={clsx(
        // Base styles
        "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
        // Default (inactive) state
        !isActive && "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
        // Active state
        isActive && (activeClassName ?? "bg-accent text-accent-foreground"),
        className
      )}
      aria-current={isActive ? "page" : undefined}
    >
      {children}
    </Link>
  );
}
