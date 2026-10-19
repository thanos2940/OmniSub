// Shared building blocks for every page, so the app reads as one thing: page header, cards, tabs,
// buttons, status chips, empty states, overflow menus and form fields. Light and dark (system setting).
import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { ChevronRight, Loader2, MoreHorizontal } from 'lucide-react';

const cx = (...parts) => parts.filter(Boolean).join(' ');

// ------------------------------------------------------------------ buttons

const BUTTON_VARIANTS = {
    primary: 'bg-indigo-600 text-white hover:bg-indigo-500 disabled:bg-indigo-600/50 shadow-sm',
    secondary: 'bg-white text-gray-800 border border-gray-250 hover:bg-gray-50 dark:bg-gray-800 dark:text-gray-100 dark:border-gray-700 dark:hover:bg-gray-750',
    ghost: 'text-gray-600 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-800 dark:hover:text-white',
    danger: 'bg-white text-rose-600 border border-rose-200 hover:bg-rose-50 dark:bg-gray-800 dark:text-rose-400 dark:border-rose-900/60 dark:hover:bg-rose-950/40',
};
const BUTTON_SIZES = {
    sm: 'h-8 px-2.5 text-xs gap-1.5',
    md: 'h-9 px-3.5 text-sm gap-2',
};

export function Button({ variant = 'secondary', size = 'md', icon: Icon, loading = false, className, children, type = 'button', ...rest }) {
    return (
        <button
            type={type}
            className={cx(
                'inline-flex items-center justify-center rounded-lg font-medium transition-colors whitespace-nowrap',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-1 dark:focus-visible:ring-offset-gray-900',
                'disabled:cursor-not-allowed disabled:opacity-60',
                BUTTON_VARIANTS[variant], BUTTON_SIZES[size], className,
            )}
            disabled={loading || rest.disabled}
            {...rest}
        >
            {loading ? <Loader2 className="animate-spin" size={size === 'sm' ? 14 : 16} /> : Icon && <Icon size={size === 'sm' ? 14 : 16} />}
            {children}
        </button>
    );
}

export function IconButton({ icon: Icon, label, className, size = 16, ...rest }) {
    return (
        <button
            type="button"
            aria-label={label}
            title={label}
            className={cx(
                'inline-flex h-8 w-8 items-center justify-center rounded-lg text-gray-500 transition-colors',
                'hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-white',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:opacity-50',
                className,
            )}
            {...rest}
        >
            <Icon size={size} />
        </button>
    );
}

// ------------------------------------------------------------------ page structure

export function Page({ children, width = 'wide', className }) {
    const max = width === 'narrow' ? 'max-w-4xl' : width === 'full' ? 'max-w-none' : 'max-w-7xl';
    return <main className={cx('mx-auto w-full px-4 py-6 sm:px-6 lg:px-8', max, className)}>{children}</main>;
}

export function PageHeader({ title, subtitle, actions, breadcrumb, children }) {
    return (
        <header className="mb-6 space-y-3">
            {breadcrumb?.length > 0 && (
                <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-sm text-gray-500 dark:text-gray-400">
                    {breadcrumb.map((crumb, i) => (
                        <span key={crumb.label} className="flex items-center gap-1">
                            {i > 0 && <ChevronRight size={14} className="text-gray-400" />}
                            {crumb.to ? <Link to={crumb.to} className="hover:text-gray-900 dark:hover:text-white">{crumb.label}</Link> : <span>{crumb.label}</span>}
                        </span>
                    ))}
                </nav>
            )}
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                    <h1 className="truncate text-2xl font-bold text-gray-900 dark:text-white">{title}</h1>
                    {subtitle && <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">{subtitle}</p>}
                </div>
                {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
            </div>
            {children}
        </header>
    );
}

export function Card({ title, subtitle, actions, children, padded = true, className, bodyClassName }) {
    return (
        <section className={cx('rounded-xl border border-gray-200 bg-white dark:border-gray-750 dark:bg-gray-850', className)}>
            {(title || actions) && (
                <div className="flex flex-wrap items-start justify-between gap-3 border-b border-gray-100 px-4 py-3 dark:border-gray-750 sm:px-5">
                    <div className="min-w-0">
                        {title && <h2 className="text-sm font-semibold text-gray-900 dark:text-white">{title}</h2>}
                        {subtitle && <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">{subtitle}</p>}
                    </div>
                    {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
                </div>
            )}
            <div className={cx(padded && 'p-4 sm:p-5', bodyClassName)}>{children}</div>
        </section>
    );
}

export function Stat({ label, value, hint, tone = 'neutral' }) {
    const toneText = { neutral: 'text-gray-900 dark:text-white', success: 'text-emerald-600 dark:text-emerald-400', warning: 'text-amber-600 dark:text-amber-400', danger: 'text-rose-600 dark:text-rose-400', accent: 'text-indigo-600 dark:text-indigo-400' }[tone];
    return (
        <div className="min-w-0">
            <div className="text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">{label}</div>
            <div className={cx('mt-1 text-xl font-semibold tabular-nums', toneText)}>{value}</div>
            {hint && <div className="mt-0.5 truncate text-xs text-gray-500 dark:text-gray-400">{hint}</div>}
        </div>
    );
}

// ------------------------------------------------------------------ tabs

// Wraps instead of scrolling sideways; below `sm` it becomes a select.
export function Tabs({ tabs, value, onChange, className }) {
    const visible = tabs.filter(t => !t.hidden);
    return (
        <div className={cx('mb-5', className)}>
            <label className="sr-only" htmlFor="tabs-select">Section</label>
            <select
                id="tabs-select"
                className={cx(INPUT, 'sm:hidden')}
                value={value}
                onChange={e => onChange(e.target.value)}
            >
                {visible.map(t => <option key={t.id} value={t.id}>{t.label}{t.count != null ? ` (${t.count})` : ''}</option>)}
            </select>
            <div role="tablist" className="hidden flex-wrap gap-1 border-b border-gray-200 dark:border-gray-750 sm:flex">
                {visible.map(t => {
                    const active = t.id === value;
                    const Icon = t.icon;
                    return (
                        <button
                            key={t.id}
                            type="button"
                            role="tab"
                            aria-selected={active}
                            onClick={() => onChange(t.id)}
                            className={cx(
                                '-mb-px inline-flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium transition-colors',
                                active
                                    ? 'border-indigo-600 text-indigo-600 dark:border-indigo-400 dark:text-indigo-400'
                                    : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200',
                            )}
                        >
                            {Icon && <Icon size={16} />}
                            {t.label}
                            {t.count != null && (
                                <span className={cx('rounded-full px-1.5 text-xs tabular-nums', active ? 'bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300' : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400')}>
                                    {t.count}
                                </span>
                            )}
                        </button>
                    );
                })}
            </div>
        </div>
    );
}

// ------------------------------------------------------------------ status

const CHIP_TONES = {
    neutral: 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300',
    success: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300',
    warning: 'bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300',
    danger: 'bg-rose-50 text-rose-700 dark:bg-rose-950/50 dark:text-rose-300',
    info: 'bg-sky-50 text-sky-700 dark:bg-sky-950/50 dark:text-sky-300',
    accent: 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950/50 dark:text-indigo-300',
};

export function StatusChip({ tone = 'neutral', icon: Icon, children, title, className }) {
    return (
        <span title={title} className={cx('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium', CHIP_TONES[tone], className)}>
            {Icon && <Icon size={12} />}
            {children}
        </span>
    );
}

export function ProgressBar({ value, max = 100, tone = 'accent', className }) {
    const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
    const bar = { accent: 'bg-indigo-500', success: 'bg-emerald-500', warning: 'bg-amber-500', danger: 'bg-rose-500' }[tone];
    return (
        <div className={cx('h-1.5 w-full overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800', className)} role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
            <div className={cx('h-full rounded-full transition-all', bar)} style={{ width: `${pct}%` }} />
        </div>
    );
}

export function EmptyState({ icon: Icon, title, description, action, className }) {
    return (
        <div className={cx('flex flex-col items-center justify-center px-6 py-12 text-center', className)}>
            {Icon && <Icon size={32} className="mb-3 text-gray-300 dark:text-gray-600" />}
            <h3 className="text-sm font-semibold text-gray-900 dark:text-white">{title}</h3>
            {description && <p className="mt-1 max-w-md text-sm text-gray-500 dark:text-gray-400">{description}</p>}
            {action && <div className="mt-4">{action}</div>}
        </div>
    );
}

// ------------------------------------------------------------------ menus

// A "…" button with a short list of secondary actions. Items: {label, icon, onClick, danger, disabled, hidden}.
export function OverflowMenu({ items, label = 'More actions', align = 'right', icon = MoreHorizontal }) {
    const [open, setOpen] = useState(false);
    const ref = useRef(null);
    const menuId = useId();
    const visible = items.filter(i => i && !i.hidden);

    useEffect(() => {
        if (!open) return undefined;
        const onDown = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
        const onKey = e => { if (e.key === 'Escape') setOpen(false); };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('mousedown', onDown);
            document.removeEventListener('keydown', onKey);
        };
    }, [open]);

    if (!visible.length) return null;
    return (
        <div className="relative inline-block" ref={ref}>
            <IconButton icon={icon} label={label} aria-haspopup="menu" aria-expanded={open} aria-controls={menuId} onClick={() => setOpen(o => !o)} />
            {open && (
                <div
                    id={menuId}
                    role="menu"
                    className={cx(
                        'absolute z-30 mt-1 min-w-[12rem] overflow-hidden rounded-lg border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-850',
                        align === 'right' ? 'right-0' : 'left-0',
                    )}
                >
                    {visible.map(item => {
                        const Icon = item.icon;
                        return (
                            <button
                                key={item.label}
                                type="button"
                                role="menuitem"
                                disabled={item.disabled}
                                onClick={() => { setOpen(false); item.onClick?.(); }}
                                className={cx(
                                    'flex w-full items-center gap-2 px-3 py-2 text-left text-sm disabled:opacity-50',
                                    item.danger
                                        ? 'text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-950/40'
                                        : 'text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-800',
                                )}
                            >
                                {Icon && <Icon size={15} className="shrink-0" />}
                                {item.label}
                            </button>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

// ------------------------------------------------------------------ forms

export const INPUT = 'block w-full rounded-lg border border-gray-250 bg-white px-3 py-2 text-sm text-gray-900 placeholder-gray-400 '
    + 'focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/30 '
    + 'dark:border-gray-700 dark:bg-gray-900 dark:text-white dark:placeholder-gray-500 disabled:opacity-60';

export function Field({ label, hint, error, children, htmlFor, className }) {
    return (
        <div className={cx('space-y-1.5', className)}>
            {label && <label htmlFor={htmlFor} className="block text-sm font-medium text-gray-700 dark:text-gray-300">{label}</label>}
            {children}
            {error ? <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>
                : hint && <p className="text-xs text-gray-500 dark:text-gray-400">{hint}</p>}
        </div>
    );
}

export function Toggle({ checked, onChange, label, description, disabled }) {
    const id = useId();
    return (
        <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
                <label htmlFor={id} className="text-sm font-medium text-gray-900 dark:text-white">{label}</label>
                {description && <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">{description}</p>}
            </div>
            <button
                id={id}
                type="button"
                role="switch"
                aria-checked={!!checked}
                disabled={disabled}
                onClick={() => onChange(!checked)}
                className={cx(
                    'relative inline-flex h-6 w-11 shrink-0 rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:opacity-50',
                    checked ? 'bg-indigo-600' : 'bg-gray-300 dark:bg-gray-700',
                )}
            >
                <span className={cx('inline-block h-5 w-5 translate-y-0.5 rounded-full bg-white shadow transition-transform', checked ? 'translate-x-5' : 'translate-x-0.5')} />
            </button>
        </div>
    );
}

// ------------------------------------------------------------------ notices

const NOTICE_TONES = {
    info: 'border-sky-200 bg-sky-50 text-sky-900 dark:border-sky-900/60 dark:bg-sky-950/40 dark:text-sky-200',
    success: 'border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900/60 dark:bg-emerald-950/40 dark:text-emerald-200',
    warning: 'border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-200',
    danger: 'border-rose-200 bg-rose-50 text-rose-900 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-200',
    accent: 'border-indigo-200 bg-indigo-50 text-indigo-900 dark:border-indigo-900/60 dark:bg-indigo-950/40 dark:text-indigo-200',
};

// An inline message strip: what happened, what is going on, or what needs attention.
export function Notice({ tone = 'info', icon: Icon, title, children, actions, className }) {
    return (
        <div className={cx('flex flex-wrap items-start justify-between gap-3 rounded-xl border px-4 py-3 text-sm', NOTICE_TONES[tone], className)} role={tone === 'danger' ? 'alert' : 'status'}>
            <div className="flex min-w-0 items-start gap-3">
                {Icon && <Icon size={18} className="mt-0.5 shrink-0" />}
                <div className="min-w-0">
                    {title && <div className="font-medium">{title}</div>}
                    {children && <div className={cx(title && 'mt-0.5 text-[13px] opacity-90')}>{children}</div>}
                </div>
            </div>
            {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
    );
}

// ------------------------------------------------------------------ feedback

export function Spinner({ label, className }) {
    return (
        <div className={cx('flex items-center justify-center gap-2 py-10 text-sm text-gray-500 dark:text-gray-400', className)} role="status">
            <Loader2 className="animate-spin" size={18} />
            {label && <span>{label}</span>}
        </div>
    );
}

// A centred dialog. Closes on Escape and on a click outside it.
export function Modal({ open, title, onClose, children, footer, width = 'md' }) {
    useEffect(() => {
        if (!open) return undefined;
        const onKey = e => { if (e.key === 'Escape') onClose?.(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [open, onClose]);

    if (!open) return null;
    const max = { sm: 'max-w-sm', md: 'max-w-md', lg: 'max-w-lg', xl: 'max-w-2xl' }[width] || 'max-w-md';
    return createPortal(
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-4" onMouseDown={e => { if (e.target === e.currentTarget) onClose?.(); }}>
            <div role="dialog" aria-modal="true" aria-label={title} className={cx('max-h-[90vh] w-full overflow-y-auto rounded-xl border border-gray-200 bg-white p-5 shadow-xl dark:border-gray-700 dark:bg-gray-850', max)}>
                <h2 className="text-base font-semibold text-gray-900 dark:text-white">{title}</h2>
                <div className="mt-3">{children}</div>
                {footer && <div className="mt-5 flex justify-end gap-2">{footer}</div>}
            </div>
        </div>,
        document.body,
    );
}

// Confirmation before something that is hard to undo. `children` carries extra detail under the message.
export function ConfirmDialog({ open, title, message, confirmLabel = 'Confirm', cancelLabel = 'Cancel', danger = false, busy = false, onConfirm, onCancel, children }) {
    return (
        <Modal
            open={open}
            title={title}
            onClose={onCancel}
            width="sm"
            footer={(
                <>
                    <Button variant="ghost" onClick={onCancel} disabled={busy}>{cancelLabel}</Button>
                    <Button variant={danger ? 'danger' : 'primary'} loading={busy} onClick={onConfirm}>{confirmLabel}</Button>
                </>
            )}
        >
            {message && <p className="text-sm text-gray-600 dark:text-gray-300">{message}</p>}
            {children}
        </Modal>
    );
}
