import React from 'react';
import { AlertTriangle, RefreshCw, Home } from 'lucide-react';

class ErrorBoundary extends React.Component {
    constructor(props) {
        super(props);
        this.state = { hasError: false, error: null, errorInfo: null };
    }

    static getDerivedStateFromError(error) {
        return { hasError: true, error };
    }

    componentDidCatch(error, errorInfo) {
        console.error("ErrorBoundary caught an unhandled error:", error, errorInfo);
        this.setState({ errorInfo });
    }

    handleReset = () => {
        this.setState({ hasError: false, error: null, errorInfo: null });
        if (this.props.onReset) {
            this.props.onReset();
        }
    };

    handleReload = () => {
        window.location.reload();
    };

    render() {
        if (this.state.hasError) {
            if (this.props.fallback) {
                return typeof this.props.fallback === 'function'
                    ? this.props.fallback({ error: this.state.error, reset: this.handleReset })
                    : this.props.fallback;
            }

            return (
                <div className="min-h-[300px] flex items-center justify-center p-6 bg-slate-50 dark:bg-gray-900">
                    <div className="max-w-md w-full bg-white dark:bg-gray-800 rounded-2xl shadow-xl border border-rose-200 dark:border-rose-900/50 p-6 text-center">
                        <div className="w-12 h-12 bg-rose-100 dark:bg-rose-950/50 rounded-full flex items-center justify-center mx-auto mb-4 text-rose-600 dark:text-rose-400">
                            <AlertTriangle size={24} />
                        </div>
                        <h3 className="text-lg font-bold text-gray-900 dark:text-white mb-2">
                            {this.props.title || "Something went wrong"}
                        </h3>
                        <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
                            {this.state.error?.message || "An unexpected error occurred while rendering this component."}
                        </p>
                        
                        {(import.meta.env?.DEV || (typeof process !== 'undefined' && process.env?.NODE_ENV !== 'production')) && this.state.errorInfo && (
                            <details className="text-left text-xs font-mono bg-gray-100 dark:bg-gray-950 p-3 rounded-lg overflow-auto max-h-32 mb-4 text-gray-700 dark:text-gray-300">
                                <summary className="cursor-pointer font-bold mb-1">Stack Trace</summary>
                                {this.state.error?.stack}
                                {'\n'}
                                {this.state.errorInfo.componentStack}
                            </details>
                        )}

                        <div className="flex items-center justify-center gap-3">
                            <button
                                onClick={this.handleReset}
                                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold rounded-xl flex items-center gap-2 transition-colors"
                            >
                                <RefreshCw size={14} /> Try Again
                            </button>
                            <button
                                onClick={this.handleReload}
                                className="px-4 py-2 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 text-sm font-semibold rounded-xl flex items-center gap-2 transition-colors"
                            >
                                Reload Page
                            </button>
                        </div>
                    </div>
                </div>
            );
        }

        return this.props.children;
    }
}

export default ErrorBoundary;
