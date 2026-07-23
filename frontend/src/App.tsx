import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import NewMeeting from './pages/NewMeeting'
import MeetingHistory from './pages/MeetingHistory'
import KnowledgeGraphs from './pages/KnowledgeGraph'
import Settings from './pages/Settings'

export default function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/new" element={<NewMeeting />} />
          <Route path="/history" element={<MeetingHistory />} />
          <Route path="/graph" element={<KnowledgeGraphs />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  )
}
