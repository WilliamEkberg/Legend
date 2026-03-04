import { Component, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";

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
        <div className="flex w-screen h-screen bg-background overflow-hidden">
          <div className="flex flex-col items-center justify-center h-full w-full gap-4 text-muted-foreground">
            <p className="text-destructive font-medium">Something went wrong rendering the map.</p>
            <p className="text-sm">
              {this.state.error?.message ?? "An unexpected error occurred."}
            </p>
            <Button variant="outline" onClick={this.handleReset}>
              Reload Map
            </Button>
            <Link to="/" className="text-sm text-primary hover:text-primary/80 mt-2">
              &larr; Back to launcher
            </Link>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
