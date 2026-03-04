import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter, Routes, Route } from 'react-router-dom'
import { ThemeProvider } from 'next-themes'
import { Toaster } from '@/components/ui/sonner'
import './index.css'
import App from './App.tsx'
import { MapView } from './components/graph/MapView.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider attribute="class" defaultTheme="dark" storageKey="legend-theme">
      <HashRouter>
        <Routes>
          <Route path="/" element={<App />} />
          <Route path="/map" element={<MapView />} />
        </Routes>
      </HashRouter>
      <Toaster />
    </ThemeProvider>
  </StrictMode>,
)
