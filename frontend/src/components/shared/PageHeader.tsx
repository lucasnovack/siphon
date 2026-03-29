interface PageHeaderProps {
  title: string
  description?: string
  action?: React.ReactNode
  breadcrumb?: React.ReactNode
}

export function PageHeader({ title, description, action, breadcrumb }: PageHeaderProps) {
  return (
    <div className="flex items-start justify-between mb-6">
      <div>
        {breadcrumb && <div className="text-sm text-muted-foreground mb-1">{breadcrumb}</div>}
        <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
        {description && <p className="text-muted-foreground mt-1">{description}</p>}
      </div>
      {action && <div>{action}</div>}
    </div>
  )
}
