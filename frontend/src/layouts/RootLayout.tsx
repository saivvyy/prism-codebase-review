import React, { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { Sidebar } from '@/components/Sidebar'
import { Header } from '@/components/Header'

interface RootLayoutProps {
  children: React.ReactNode
}

export const RootLayout: React.FC<RootLayoutProps> = ({ children }) => {
  const location = useLocation()

  useEffect(() => {
    const fullPath = location.pathname + location.search + location.hash
    // Track active authenticated workspace location
    if (
      fullPath &&
      !fullPath.startsWith('/repositories/select') &&
      !fullPath.startsWith('/auth') &&
      fullPath !== '/' &&
      fullPath !== '/landing'
    ) {
      try {
        sessionStorage.setItem('prism_last_workspace_location', fullPath)
      } catch {
        // Ignore storage restrictions
      }
    }
  }, [location])

  return (
    <div className="min-h-screen bg-[#0d1117] text-[#c9d1d9] flex flex-col font-sans selection:bg-[#1f6feb]/30 selection:text-[#58a6ff]">
      <Header />
      <div className="flex-1 flex flex-row min-w-0">
        <Sidebar />
        <main className="flex-1 px-6 py-5 overflow-y-auto min-w-0">
          <div className="max-w-[1280px] mx-auto w-full">
            {children}
          </div>
        </main>
      </div>
    </div>
  )
}
