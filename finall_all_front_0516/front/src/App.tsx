import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Layout from './components/layout/Layout'
import Dashboard from './pages/Dashboard'
import SearchDownload from './pages/SearchDownload'
import PdfParsing from './pages/PdfParsing'
import AnalysisReport from './pages/AnalysisReport'
import FactVerification from './pages/FactVerification'
import Tracking from './pages/Tracking'
import LegalCompliance from './pages/LegalCompliance'
import ChatPage from './pages/ChatPage'
import Settings from './pages/Settings'
import Help from './pages/Help'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/search" element={<SearchDownload />} />
          <Route path="/parse" element={<PdfParsing />} />
          <Route path="/analysis" element={<AnalysisReport />} />
          <Route path="/verify" element={<FactVerification />} />
          <Route path="/tracking" element={<Tracking />} />
          <Route path="/legal" element={<LegalCompliance />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/help" element={<Help />} />
          <Route path="/chat" element={<ChatPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
