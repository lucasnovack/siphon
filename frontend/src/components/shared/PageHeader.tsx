interface PageHeaderProps {
  title: string
  description?: React.ReactNode
  action?: React.ReactNode
  breadcrumb?: React.ReactNode
}

export function PageHeader({ title, description, action, breadcrumb }: PageHeaderProps) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between pb-6 mb-6 border-b border-border/60">
      <div className="space-y-1">
        {breadcrumb && <div className="text-[11px] text-muted-foreground mb-1.5">{breadcrumb}</div>}
        <h1 className="text-2xl font-bold tracking-tight text-foreground">{title}</h1>
        {description && <div className="text-sm text-muted-foreground">{description}</div>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  )
}
