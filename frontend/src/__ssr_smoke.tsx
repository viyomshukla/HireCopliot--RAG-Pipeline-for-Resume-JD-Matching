import { renderToString } from 'react-dom/server'
import App from './App'
const html = renderToString(<App />)
console.log('SSR_LENGTH=' + html.length)
console.log('HAS_BRAND=' + html.includes('HireMind'))
console.log('HAS_NAV=' + (html.includes('Batches') && html.includes('Fairness')))
console.log('HAS_DISCLAIMER=' + html.includes('does not decide who to hire'))
