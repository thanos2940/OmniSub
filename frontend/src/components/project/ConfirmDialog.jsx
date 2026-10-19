import React, { useEffect, useState } from 'react';
import { Button } from '../ui';

// A small confirmation modal. `checkbox` ({label, defaultChecked}) adds one option whose value is passed
// to onConfirm(checked).
const ConfirmDialog = ({ open, title, children, confirmLabel = 'Confirm', danger = false, checkbox, busy = false, onConfirm, onCancel }) => {
    const [checked, setChecked] = useState(false);

    useEffect(() => {
        if (open) setChecked(!!checkbox?.defaultChecked);
    }, [open, checkbox?.defaultChecked]);

    useEffect(() => {
        if (!open) return undefined;
        const onKey = (e) => { if (e.key === 'Escape') onCancel?.(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [open, onCancel]);

    if (!open) return null;
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel?.(); }}>
            <div role="dialog" aria-modal="true" aria-label={title} className="w-full max-w-md rounded-xl border border-gray-200 bg-white p-5 shadow-xl dark:border-gray-700 dark:bg-gray-850">
                <h2 className="text-base font-semibold text-gray-900 dark:text-white">{title}</h2>
                <div className="mt-2 space-y-3 text-sm text-gray-600 dark:text-gray-300">{children}</div>
                {checkbox && (
                    <label className="mt-4 flex cursor-pointer items-start gap-2 text-sm text-gray-700 dark:text-gray-200">
                        <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} className="mt-0.5 h-4 w-4 rounded border-gray-300 text-indigo-600" />
                        <span>{checkbox.label}</span>
                    </label>
                )}
                <div className="mt-5 flex justify-end gap-2">
                    <Button onClick={onCancel} disabled={busy}>Cancel</Button>
                    <Button variant={danger ? 'danger' : 'primary'} loading={busy} onClick={() => onConfirm?.(checked)}>{confirmLabel}</Button>
                </div>
            </div>
        </div>
    );
};

export default ConfirmDialog;
