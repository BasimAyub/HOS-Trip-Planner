import React from "react";
import { createRoot } from "react-dom/client";
import "leaflet/dist/leaflet.css";
import "./styles.css";
import App from "./App.jsx";

class RootErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 24, fontFamily: "system-ui", maxWidth: 640 }}>
          <h1 style={{ marginTop: 0 }}>Something went wrong</h1>
          <pre style={{ whiteSpace: "pre-wrap", background: "#f5f5f5", padding: 12, borderRadius: 8 }}>
            {String(this.state.error?.message || this.state.error)}
          </pre>
          <p style={{ color: "#444" }}>
            Open the browser developer console (F12 → Console) for the full stack trace. If you opened{" "}
            <code>dist/index.html</code> from disk, run <code>npm run dev</code> or <code>npm run preview</code>{" "}
            instead.
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <RootErrorBoundary>
      <App />
    </RootErrorBoundary>
  </React.StrictMode>,
);
