import { Loader2 } from 'lucide-react';

const LoadingFallback = ({ message = 'Loading...', fullScreen = false, size = 'default' }) => {
    const spinnerSize = size === 'large' ? 36 : size === 'small' ? 18 : 24;

    const content = (
        <div className="flex flex-col items-center justify-center p-8 text-gray-500 dark:text-gray-400">
            <Loader2 size={spinnerSize} className="animate-spin text-indigo-600 dark:text-indigo-400 mb-3" />
            {message && <p className="text-sm font-medium">{message}</p>}
        </div>
    );

    if (fullScreen) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-slate-50 dark:bg-gray-900">
                {content}
            </div>
        );
    }

    return content;
};

export default LoadingFallback;
