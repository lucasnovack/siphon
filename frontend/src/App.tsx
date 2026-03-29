import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AppLayout } from '@/components/layout/AppLayout'
import { RequireAuth } from '@/components/layout/RequireAuth'
import { LoginPage } from '@/pages/LoginPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { ConnectionsPage } from '@/pages/connections/ConnectionsPage'
import { ConnectionNewPage } from '@/pages/connections/ConnectionNewPage'
import { ConnectionDetailPage } from '@/pages/connections/ConnectionDetailPage'
import { PipelinesPage } from '@/pages/pipelines/PipelinesPage'
import { PipelineWizard } from '@/pages/pipelines/PipelineWizard'
import { PipelineDetailPage } from '@/pages/pipelines/PipelineDetailPage'
import { PipelineEditPage } from '@/pages/pipelines/PipelineEditPage'
import { RunsPage } from '@/pages/runs/RunsPage'
import { RunDetailPage } from '@/pages/runs/RunDetailPage'
import { UsersPage } from '@/pages/settings/UsersPage'
import { SystemPage } from '@/pages/settings/SystemPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />

        <Route
          element={
            <RequireAuth>
              <AppLayout />
            </RequireAuth>
          }
        >
          <Route index element={<DashboardPage />} />

          <Route path="connections">
            <Route index element={<ConnectionsPage />} />
            <Route path="new" element={<ConnectionNewPage />} />
            <Route path=":id" element={<ConnectionDetailPage />} />
          </Route>

          <Route path="pipelines">
            <Route index element={<PipelinesPage />} />
            <Route path="new" element={<PipelineWizard />} />
            <Route path=":id" element={<PipelineDetailPage />} />
            <Route path=":id/edit" element={<PipelineEditPage />} />
          </Route>

          <Route path="runs">
            <Route index element={<RunsPage />} />
            <Route path=":id" element={<RunDetailPage />} />
          </Route>

          <Route path="settings">
            <Route index element={<Navigate to="/settings/system" replace />} />
            <Route path="users" element={<UsersPage />} />
            <Route path="system" element={<SystemPage />} />
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
