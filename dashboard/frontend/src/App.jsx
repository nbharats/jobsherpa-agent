import { useEffect, useState } from 'react'
import Overview from './pages/Overview.jsx'
import Jobs from './pages/Jobs.jsx'
import Emails from './pages/Emails.jsx'
import Logs from './pages/Logs.jsx'
import Report from './pages/Report.jsx'

const PAGES = [
  { id: 'overview', label: 'Overview', component: Overview },
  { id: 'jobs', label: 'Jobs', component: Jobs },
  { id: 'emails', label: 'Emails', component: Emails },
  { id: 'logs', label: 'Logs', component: Logs },
  { id: 'report', label: 'Report', component: Report },
]

function currentPage() {
  const id = window.location.hash.replace('#/', '')
  return PAGES.find((page) => page.id === id) ?? PAGES[0]
}

function loadTheme() {
  try {
    return localStorage.getItem('theme') === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

export default function App() {
  const [page, setPage] = useState(currentPage)
  const [theme, setTheme] = useState(loadTheme)

  useEffect(() => {
    const onChange = () => setPage(currentPage())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem('theme', theme)
    } catch {
      // Storage can be blocked; the theme then lasts for this visit only.
    }
  }, [theme])

  const Page = page.component
  return (
    <>
      <header>
        <h1>JobSherpa</h1>
        <nav>
          {PAGES.map((item) => (
            <a key={item.id} href={`#/${item.id}`} className={item.id === page.id ? 'active' : ''}>
              {item.label}
            </a>
          ))}
        </nav>
        <button type="button" className="theme" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
          {theme === 'dark' ? 'Light mode' : 'Dark mode'}
        </button>
      </header>
      <main>
        <Page />
      </main>
    </>
  )
}