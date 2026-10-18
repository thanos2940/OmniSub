import React, { createContext, useContext, useState, useCallback, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { CheckCircle, XCircle, Info, X } from 'lucide-react';

const ToastContext = createContext(null);

export const useToast = () => {
    const context = useContext(ToastContext);
    if (!context) {
        throw new Error('useToast must be used within a ToastProvider');
    }
    return context;
};

export const formatErrorMessage = (err) => {
    if (!err) return '';
    if (typeof err === 'string') return err;
    if (Array.isArray(err)) {
        return err.map(e => (typeof e === 'object' && e?.msg ? `${e.loc ? e.loc.join('.') + ': ' : ''}${e.msg}` : String(e))).join(', ');
    }
    if (typeof err === 'object') {
        if (err.message && typeof err.message === 'string') return err.message;
        if (err.detail) return formatErrorMessage(err.detail);
        if (err.error && typeof err.error === 'string') return err.error;
        try {
            return JSON.stringify(err);
        } catch {
            return String(err);
        }
    }
    return String(err);
};

let toastId = 0;

export const ToastProvider = ({ children }) => {
    const [toasts, setToasts] = useState([]);

    const addToast = useCallback((rawMessage, type = 'info', duration = 4000) => {
        const id = ++toastId;
        const message = formatErrorMessage(rawMessage);
        setToasts(prev => [...prev, { id, message, type }]);
        if (duration > 0) {
            setTimeout(() => {
                setToasts(prev => prev.filter(t => t.id !== id));
            }, duration);
        }
        return id;
    }, []);

    const removeToast = useCallback((id) => {
        setToasts(prev => prev.filter(t => t.id !== id));
    }, []);

    const toast = useMemo(() => ({
        success: (msg, duration) => addToast(msg, 'success', duration),
        error: (msg, duration) => addToast(msg, 'error', duration ?? 6000),
        info: (msg, duration) => addToast(msg, 'info', duration),
        warning: (msg, duration) => addToast(msg, 'warning', duration ?? 5000),
    }), [addToast]);

    const icons = {
        success: <CheckCircle size={18} className="text-green-400 shrink-0" />,
        error: <XCircle size={18} className="text-red-400 shrink-0" />,
        info: <Info size={18} className="text-blue-400 shrink-0" />,
        warning: <Info size={18} className="text-amber-400 shrink-0" />,
    };

    const bgColors = {
        success: 'bg-green-900/90 border-green-700',
        error: 'bg-red-900/90 border-red-700',
        info: 'bg-gray-800/90 border-gray-600',
        warning: 'bg-amber-900/90 border-amber-700',
    };

    return (
        <ToastContext.Provider value={toast}>
            {children}
            <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 pointer-events-none">
                <AnimatePresence>
                    {toasts.map(t => (
                        <motion.div
                            key={t.id}
                            initial={{ opacity: 0, y: 20, scale: 0.95 }}
                            animate={{ opacity: 1, y: 0, scale: 1 }}
                            exit={{ opacity: 0, x: 100, scale: 0.95 }}
                            className={`pointer-events-auto flex items-center gap-3 px-4 py-3 rounded-xl border backdrop-blur-lg shadow-xl max-w-sm ${bgColors[t.type]}`}
                        >
                            {icons[t.type]}
                            <span className="text-sm text-white flex-1">{t.message}</span>
                            <button onClick={() => removeToast(t.id)} className="text-gray-400 hover:text-white shrink-0">
                                <X size={14} />
                            </button>
                        </motion.div>
                    ))}
                </AnimatePresence>
            </div>
        </ToastContext.Provider>
    );
};
