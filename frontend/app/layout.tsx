export const metadata = { title: "RepoMind — CI Auto-Fix Agent", description: "AI-powered multi-agent CI fix system" };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ margin: 0, background: "#ffffff", color: "#111827" }}>{children}</body>
    </html>
  );
}
