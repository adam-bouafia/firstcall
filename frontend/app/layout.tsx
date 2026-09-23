import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FirstCall",
  description: "Kubernetes incident first-responder on open-weight models",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" />
      </head>
      <body className="min-h-screen antialiased">
        <script dangerouslySetInnerHTML={{ __html: "try{var t=localStorage.getItem('firstcall-theme');if(t)document.documentElement.setAttribute('data-theme',t)}catch(e){}" }} />
        {children}
      </body>
    </html>
  );
}
