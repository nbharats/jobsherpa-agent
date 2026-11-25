// The server only sends a link for http(s) URLs, so an empty link means plain text.
export default function JobLink({ link, title }) {
  if (!link) return <>{title}</>
  return (
    <a href={link} target="_blank" rel="noopener noreferrer">
      {title}
    </a>
  )
}