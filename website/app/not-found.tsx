export default function NotFound() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh', fontFamily: 'var(--font-mono)' }}>
      <div style={{ textAlign: 'center' }}>
        <h1 style={{ fontSize: '2rem', fontWeight: 500, marginBottom: '0.5rem' }}>404</h1>
        <p style={{ color: 'var(--color-text)' }}>Page not found</p>
        <a href="/" style={{ color: 'var(--color-text-strong)', marginTop: '1rem', display: 'inline-block' }}>Back to home</a>
      </div>
    </div>
  );
}
