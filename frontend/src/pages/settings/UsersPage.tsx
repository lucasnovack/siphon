import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { format } from 'date-fns'
import { Plus, Trash2, UserCog } from 'lucide-react'
import { usersApi, type User } from '@/lib/api'
import { useAuth } from '@/contexts/AuthContext'
import { PageHeader } from '@/components/shared/PageHeader'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import { ApiErrorMessage } from '@/components/shared/ApiErrorMessage'
import { EmptyState } from '@/components/shared/EmptyState'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { toast } from '@/hooks/use-toast'

const createSchema = z.object({
  email: z.string().email('Invalid email'),
  name: z.string().optional(),
  password: z.string().min(8, 'Minimum 8 characters'),
  role: z.enum(['admin', 'operator']),
})

type CreateForm = z.infer<typeof createSchema>

export function UsersPage() {
  const { user: me } = useAuth()
  const qc = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)

  const { data: users, isLoading, error } = useQuery({
    queryKey: ['users'],
    queryFn: () => usersApi.list().then((r) => r.data),
  })

  const { register, handleSubmit, setValue, watch, reset, formState: { errors } } = useForm<CreateForm>({
    resolver: zodResolver(createSchema),
    defaultValues: { email: '', name: '', password: '', role: 'operator' },
  })

  const createMutation = useMutation({
    mutationFn: (data: unknown) => usersApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['users'] })
      toast({ title: 'User created' })
      setShowCreate(false)
      reset()
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => usersApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['users'] })
      toast({ title: 'User deleted' })
    },
  })

  if (me?.role !== 'admin') {
    return (
      <div className="max-w-2xl">
        <PageHeader title="Users" />
        <p className="text-muted-foreground">Admin access required.</p>
      </div>
    )
  }

  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader
        title="Users"
        action={
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4 mr-2" />
            New User
          </Button>
        }
      />

      {isLoading && <div className="flex justify-center py-12"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" /></div>}
      {error && <ApiErrorMessage error={error} />}

      {!isLoading && !error && (
        <Card>
          {(users ?? []).length === 0 ? (
            <CardContent className="py-8">
              <EmptyState icon={UserCog} title="No users yet" description="Create the first user to get started." />
            </CardContent>
          ) : (
            <div className="divide-y">
              {(users ?? []).map((u: User) => (
                <div key={u.id} className="flex items-center justify-between px-6 py-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm">{u.name ?? u.email}</span>
                      <Badge variant={u.role === 'admin' ? 'default' : 'secondary'} className="text-xs">
                        {u.role}
                      </Badge>
                      {!u.is_active && <Badge variant="outline" className="text-xs">inactive</Badge>}
                      {u.id === me?.id && <Badge variant="outline" className="text-xs">you</Badge>}
                    </div>
                    <div className="text-xs text-muted-foreground mt-0.5">
                      {u.email} · joined {format(new Date(u.created_at), 'MMM d, yyyy')}
                    </div>
                  </div>
                  {u.id !== me?.id && (
                    <ConfirmDialog
                      trigger={
                        <Button variant="ghost" size="icon" className="text-muted-foreground hover:text-destructive">
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      }
                      title={`Delete ${u.name ?? u.email}?`}
                      description="This action cannot be undone."
                      confirmLabel="Delete"
                      destructive
                      onConfirm={() => deleteMutation.mutate(u.id)}
                    />
                  )}
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New User</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSubmit((d) => createMutation.mutate(d))} className="space-y-4">
            <div className="space-y-2">
              <Label>Email</Label>
              <Input type="email" placeholder="user@example.com" {...register('email')} />
              {errors.email && <p className="text-xs text-destructive">{errors.email.message}</p>}
            </div>
            <div className="space-y-2">
              <Label>Name <span className="text-muted-foreground text-xs">(optional)</span></Label>
              <Input placeholder="Full name" {...register('name')} />
            </div>
            <div className="space-y-2">
              <Label>Password</Label>
              <Input type="password" {...register('password')} />
              {errors.password && <p className="text-xs text-destructive">{errors.password.message}</p>}
            </div>
            <div className="space-y-2">
              <Label>Role</Label>
              <Select value={watch('role')} onValueChange={(v) => setValue('role', v as 'admin' | 'operator')}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="operator">Operator</SelectItem>
                  <SelectItem value="admin">Admin</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {createMutation.error && <ApiErrorMessage error={createMutation.error} />}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => { setShowCreate(false); reset() }}>
                Cancel
              </Button>
              <Button type="submit" disabled={createMutation.isPending}>
                {createMutation.isPending ? 'Creating…' : 'Create'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
