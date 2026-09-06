import React from 'react'
import ReactDOM from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { AuthProvider } from './auth'
import { RequireAuth, Layout } from './Layout'
import LoginPage from './pages/Login'
import RecordsPage from './pages/Records'
import RecordViewPage from './pages/RecordView'
import UploadPage from './pages/Upload'
import AuditPage from './pages/Audit'
import './styles.css'

const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    element: <RequireAuth><Layout /></RequireAuth>,
    children: [
      { path: '/', element: <RecordsPage /> },
      { path: '/records', element: <RecordsPage /> },
      { path: '/records/:id', element: <RecordViewPage /> },
      { path: '/upload', element: <UploadPage /> },
      { path: '/audit', element: <AuditPage /> },
    ],
  },
])

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  </React.StrictMode>,
)
