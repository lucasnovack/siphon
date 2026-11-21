import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom'
import {
  Activity,
  Database,
  GitBranch,
  LayoutDashboard,
  LogOut,
  Monitor,
  Moon,
  Settings,
  Sun,
  Users,
} from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { useTheme, type Theme } from '@/contexts/ThemeContext'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

const navItems = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/connections', label: 'Connections', icon: Database },
  { to: '/pipelines', label: 'Pipelines', icon: GitBranch },
  { to: '/runs', label: 'Runs', icon: Activity },
]

const settingsItems = [
  { to: '/settings/users', label: 'Users', icon: Users, adminOnly: true },
  { to: '/settings/system', label: 'System', icon: Settings, adminOnly: false },
]

const themeOptions: { value: Theme; icon: typeof Sun; label: string }[] = [
  { value: 'light', icon: Sun, label: 'Light theme' },
  { value: 'system', icon: Monitor, label: 'System theme' },
  { value: 'dark', icon: Moon, label: 'Dark theme' },
]

export function AppLayout() {
  const { user, logout } = useAuth()
  const { theme, setTheme } = useTheme()
  const navigate = useNavigate()

  async function handleLogout() {
    await logout()
    navigate('/login')
  }

  return (
    <div className="flex h-screen bg-background text-foreground">
      {/* Modern translucent sidebar */}
      <aside className="w-64 border-r border-border/70 bg-card/70 backdrop-blur-xl flex flex-col shrink-0 select-none">
        {/* Brand header */}
        <div className="h-14 px-5 flex items-center justify-between border-b border-border/60">
          <Link to="/" className="flex items-center gap-2.5 group">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary text-primary-foreground text-sm font-bold shadow-apple-sm transition-transform duration-150 group-hover:scale-105">
              ⟂
            </span>
            <span className="font-semibold text-base tracking-tight">siphon</span>
          </Link>
          <span className="text-[11px] font-medium text-muted-foreground/80 px-2 py-0.5 rounded-md bg-secondary/80 border border-border/60 font-mono">
            bronze
          </span>
        </div>

        {/* Navigation groups */}
        <nav className="flex-1 px-3 py-4 space-y-6 overflow-y-auto">
          <div>
            <div className="px-2.5 mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/75">
              Workspace
            </div>
            <div className="space-y-1">
              {navItems.map(({ to, label, icon: Icon, end }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={end}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-3 px-3 py-2.5 rounded-xl text-[13.5px] transition-all duration-150',
                      isActive
                        ? 'bg-foreground/[0.08] text-foreground font-semibold shadow-[inset_0_0_0_1px_hsl(var(--border)/0.7)]'
                        : 'text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground font-medium',
                    )
                  }
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  <span>{label}</span>
                </NavLink>
              ))}
            </div>
          </div>

          <div>
            <div className="px-2.5 mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/75">
              Settings
            </div>
            <div className="space-y-1">
              {settingsItems
                .filter((item) => !item.adminOnly || user?.role === 'admin')
                .map(({ to, label, icon: Icon }) => (
                  <NavLink
                    key={to}
                    to={to}
                    className={({ isActive }) =>
                      cn(
                        'flex items-center gap-3 px-3 py-2.5 rounded-xl text-[13.5px] transition-all duration-150',
                        isActive
                          ? 'bg-foreground/[0.08] text-foreground font-semibold shadow-[inset_0_0_0_1px_hsl(var(--border)/0.7)]'
                          : 'text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground font-medium',
                      )
                    }
                  >
                    <Icon className="h-4 w-4 shrink-0" />
                    <span>{label}</span>
                  </NavLink>
                ))}
            </div>
          </div>
        </nav>

        {/* Footer: theme switcher + user */}
        <div className="p-3.5 border-t border-border/60 space-y-3">
          {/* Apple-style segmented theme control */}
          <div
            role="group"
            aria-label="Theme"
            className="grid grid-cols-3 gap-0.5 p-0.5 rounded-lg bg-secondary/80 border border-border/60"
          >
            {themeOptions.map(({ value, icon: Icon, label }) => {
              const active = theme === value
              return (
                <button
                  key={value}
                  type="button"
                  onClick={() => setTheme(value)}
                  aria-label={label}
                  aria-pressed={active}
                  title={label}
                  className={cn(
                    'flex items-center justify-center h-7 rounded-md transition-all duration-150',
                    active
                      ? 'bg-card text-foreground shadow-apple-sm'
                      : 'text-muted-foreground hover:text-foreground',
                  )}
                >
                  <Icon className="h-3.5 w-3.5" />
                </button>
              )
            })}
          </div>

          <div className="flex items-center justify-between px-1">
            <div className="min-w-0">
              <div className="text-xs font-semibold truncate max-w-[150px]">
                {user?.name ?? user?.email}
              </div>
              <div className="text-[11px] text-muted-foreground capitalize font-mono">
                {user?.role}
              </div>
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={handleLogout}
              title="Sign out"
              aria-label="Sign out"
              className="text-muted-foreground hover:text-foreground h-8 w-8"
            >
              <LogOut className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <div className="mx-auto max-w-[1360px] py-8 px-6 sm:px-10">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
