import { Component, type ReactNode } from "react";
import { Link } from "react-router-dom";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class MapErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("[MapErrorBoundary] Caught render error:", error, info.componentStack);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="map-container">
          <div className="map-empty">
            <p className="map-error">Something went wrong rendering the map.</p>
            <p className="map-empty-hint">
              {this.state.error?.message ?? "An unexpected error occurred."}
            </p>
            <button className="sidebar-btn" onClick={this.handleReset}>
              Reload Map
            </button>
            <Link to="/" className="map-back-link" style={{ marginTop: 12 }}>
              &larr; Back to launcher
            </Link>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
