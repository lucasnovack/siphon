import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom'
import { Activity, Database, GitBranch, LayoutDashboard, LogOut, Settings, Users } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
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
]

export function AppLayout() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  async function handleLogout() {
    await logout()
    navigate('/login')
  }

  return (
    <div className="flex h-screen bg-background">
      {/* Sidebar */}
      <aside className="w-56 border-r flex flex-col shrink-0">
        <div className="p-4 border-b">
          <Link to="/" className="flex items-center gap-2 font-bold text-lg">
            <span className="text-primary">⟂</span> Siphon
          </Link>
        </div>

        <nav className="flex-1 p-2 space-y-1">
          {navItems.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors',
                  isActive
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                )
              }
            >
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          ))}

          <div className="pt-4 border-t mt-4">
            {settingsItems
              .filter((item) => !item.adminOnly || user?.role === 'admin')
              .map(({ to, label, icon: Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors',
                      isActive
                        ? 'bg-primary text-primary-foreground'
                        : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                    )
                  }
                >
                  <Icon className="h-4 w-4" />
                  {label}
                </NavLink>
              ))}
            <NavLink
              to="/settings/system"
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors',
                  isActive
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                )
              }
            >
              <Settings className="h-4 w-4" />
              System
            </NavLink>
          </div>
        </nav>

        <div className="p-4 border-t">
          <div className="flex items-center justify-between">
            <div className="text-sm">
              <div className="font-medium truncate max-w-[120px]">{user?.name ?? user?.email}</div>
              <div className="text-xs text-muted-foreground capitalize">{user?.role}</div>
            </div>
            <Button variant="ghost" size="icon" onClick={handleLogout} title="Logout">
              <LogOut className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <div className="container max-w-6xl py-8 px-6">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
