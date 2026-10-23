import React from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ThemeProvider, BaseStyles } from '@primer/react'
import { AuthProvider } from '@/context/AuthContext'
import { RepoProvider } from '@/context/RepoContext'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { RootLayout } from '@/layouts/RootLayout'
import { LandingPage } from '@/pages/LandingPage'
import { OAuthCallbackPage } from '@/pages/OAuthCallbackPage'
import { RepositoriesSelectPage } from '@/pages/RepositoriesSelectPage'
import { GlobalHomePage } from '@/pages/GlobalHomePage'
import { OverviewPage } from '@/pages/OverviewPage'
import { RepositoriesPage } from '@/pages/RepositoriesPage'
import { PullRequestsPage } from '@/pages/PullRequestsPage'
import { PullRequestReviewPage } from '@/pages/PullRequestReviewPage'
import { MemoryExplorerPage } from '@/pages/MemoryExplorerPage'
import { SettingsPage } from '@/pages/SettingsPage'

export const App: React.FC = () => {
  return (
    <ThemeProvider colorMode="dark" nightScheme="dark">
      <BaseStyles>
        <BrowserRouter>
          <AuthProvider>
            <RepoProvider>
              <Routes>
                {/* Public Unauthenticated PRism Landing & OAuth Callback */}
                <Route path="/" element={<LandingPage />} />
                <Route path="/landing" element={<LandingPage />} />
                <Route path="/auth/callback" element={<OAuthCallbackPage />} />

                {/* Authenticated Repository Selection (Step 2 of login) */}
                <Route
                  path="/repositories/select"
                  element={
                    <ProtectedRoute>
                      <RepositoriesSelectPage />
                    </ProtectedRoute>
                  }
                />

                {/* Authenticated PRism Workspace (wrapped in ProtectedRoute & RootLayout) */}
                <Route
                  path="/workspace"
                  element={
                    <ProtectedRoute>
                      <RootLayout>
                        <GlobalHomePage />
                      </RootLayout>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/overview"
                  element={
                    <ProtectedRoute>
                      <RootLayout>
                        <OverviewPage />
                      </RootLayout>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/repositories"
                  element={
                    <ProtectedRoute>
                      <RootLayout>
                        <RepositoriesPage />
                      </RootLayout>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/pull-requests"
                  element={
                    <ProtectedRoute>
                      <RootLayout>
                        <PullRequestsPage />
                      </RootLayout>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/pull-requests/:repoId/:prNumber"
                  element={
                    <ProtectedRoute>
                      <RootLayout>
                        <PullRequestReviewPage />
                      </RootLayout>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/memory"
                  element={
                    <ProtectedRoute>
                      <RootLayout>
                        <MemoryExplorerPage />
                      </RootLayout>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/settings"
                  element={
                    <ProtectedRoute>
                      <RootLayout>
                        <SettingsPage />
                      </RootLayout>
                    </ProtectedRoute>
                  }
                />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </RepoProvider>
          </AuthProvider>
        </BrowserRouter>
      </BaseStyles>
    </ThemeProvider>
  )
}

export default App
