// Small pieces shared by the Settings sections, built on the shared UI kit.
import { useState, useId } from 'react';
import { AlertTriangle, CheckCircle2, ChevronDown, Copy, Check, Eye, EyeOff, Info, XCircle } from 'lucide-react';
import { INPUT } from '../ui';

export const SECRET_SENTINEL = '__SECRET_UNCHANGED__';

export const cx = (...parts) => parts.filter(Boolean).join(' ');

export const errorText = (err, fallback = 'Something went wrong') => {
    const detail = err?.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (detail?.message) return detail.message;
    return err?.response?.data?.error || err?.message || fallback;
};

// A number that can be cleared while typing; the value is committed (clamped) as soon as it parses.
export function NumberInput({ value, onChange, min, max, step = 1, className, ...rest }) {
    const parse = (raw) => {
        const n = Number(raw);
        if (raw === '' || Number.isNaN(n)) return null;
        return Math.min(max ?? Infinity, Math.max(min ?? -Infinity, n));
    };
    const [draft, setDraft] = useState(null);
    return (
        <input
            type="number"
            inputMode="decimal"
            min={min}
            max={max}
            step={step}
            value={draft ?? value ?? ''}
            onChange={(e) => {
                setDraft(e.target.value);
                const n = parse(e.target.value);
                if (n !== null) onChange(n);
            }}
            onBlur={() => setDraft(null)}
            className={cx(INPUT, 'w-28', className)}
            {...rest}
        />
    );
}

// A secret the server never sends back: it arrives as SECRET_SENTINEL ("saved"). Typing replaces it;
// clearing the box puts the sentinel back, which the server reads as "keep what is stored".
export function SecretInput({ value, onChange, isSet, placeholder, id, mono = true }) {
    const [shown, setShown] = useState(false);
    const kept = value === SECRET_SENTINEL;
    return (
        <div className="relative">
            <input
                id={id}
                type={shown ? 'text' : 'password'}
                autoComplete="off"
                spellCheck={false}
                value={kept ? '' : (value || '')}
                placeholder={kept || isSet ? 'Saved. Type to replace.' : placeholder}
                onChange={(e) => onChange(e.target.value === '' && (kept || isSet) ? SECRET_SENTINEL : e.target.value)}
                className={cx(INPUT, 'pr-10', mono && 'font-mono')}
            />
            <button
                type="button"
                onClick={() => setShown(s => !s)}
                aria-label={shown ? 'Hide' : 'Show'}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
            >
                {shown ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
        </div>
    );
}

const NOTICE_TONES = {
    info: 'border-sky-200 bg-sky-50 text-sky-900 dark:border-sky-900/50 dark:bg-sky-950/30 dark:text-sky-200',
    warning: 'border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200',
    success: 'border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900/50 dark:bg-emerald-950/30 dark:text-emerald-200',
    danger: 'border-rose-200 bg-rose-50 text-rose-900 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-200',
};
const NOTICE_ICONS = { info: Info, warning: AlertTriangle, success: CheckCircle2, danger: XCircle };

export function Notice({ tone = 'info', children, className }) {
    const Icon = NOTICE_ICONS[tone];
    return (
        <div className={cx('flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-sm', NOTICE_TONES[tone], className)}>
            <Icon size={16} className="mt-0.5 shrink-0" />
            <div className="min-w-0 space-y-1">{children}</div>
        </div>
    );
}

// Outcome of a Test button: { ok, text } or null.
export function TestResult({ result }) {
    if (!result) return null;
    return <Notice tone={result.ok ? 'success' : 'danger'}>{result.text}</Notice>;
}

export function CopyButton({ text, label = 'Copy', onCopied }) {
    const [done, setDone] = useState(false);
    const copy = async () => {
        try {
            await navigator.clipboard.writeText(text);
            setDone(true);
            onCopied?.();
            setTimeout(() => setDone(false), 1500);
        } catch { /* clipboard blocked: the text stays selectable */ }
    };
    return (
        <button
            type="button"
            onClick={copy}
            className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-indigo-600 hover:bg-indigo-50 dark:text-indigo-400 dark:hover:bg-indigo-950/40"
        >
            {done ? <Check size={14} /> : <Copy size={14} />}
            {done ? 'Copied' : label}
        </button>
    );
}

// A card whose body is hidden until opened (for diagnostics and advanced options).
export function Disclosure({ title, subtitle, children, defaultOpen = false }) {
    const [open, setOpen] = useState(defaultOpen);
    const panelId = useId();
    return (
        <section className="rounded-xl border border-gray-200 bg-white dark:border-gray-750 dark:bg-gray-850">
            <button
                type="button"
                onClick={() => setOpen(o => !o)}
                aria-expanded={open}
                aria-controls={panelId}
                className="flex w-full items-center justify-between gap-3 rounded-xl px-4 py-3 text-left sm:px-5"
            >
                <span className="min-w-0">
                    <span className="block text-sm font-semibold text-gray-900 dark:text-white">{title}</span>
                    {subtitle && <span className="mt-0.5 block text-xs text-gray-500 dark:text-gray-400">{subtitle}</span>}
                </span>
                <ChevronDown size={18} className={cx('shrink-0 text-gray-400 transition-transform', open && 'rotate-180')} />
            </button>
            {open && (
                <div id={panelId} className="space-y-5 border-t border-gray-100 p-4 dark:border-gray-750 sm:p-5">
                    {children}
                </div>
            )}
        </section>
    );
}

// A labelled range input with its current value.
// A range input. With `unsetLabel`, the value may be empty (null): the chip then shows `unsetLabel` (e.g.
// "Google default"), the thumb rests at `unsetAt`, dragging sets a value, and Reset empties it again.
export function Slider({ label, hint, value, min, max, step, onChange, format = (v) => v, unsetLabel, unsetAt }) {
    const id = useId();
    const unset = unsetLabel !== undefined && (value === null || value === undefined || value === '');
    return (
        <div className="space-y-1.5">
            <div className="flex items-center justify-between gap-3">
                <label htmlFor={id} className="text-sm font-medium text-gray-700 dark:text-gray-300">{label}</label>
                <span className="flex items-center gap-2">
                    {unsetLabel !== undefined && !unset && (
                        <button type="button" onClick={() => onChange(null)} className="text-xs font-medium text-indigo-600 hover:underline dark:text-indigo-400">
                            Reset
                        </button>
                    )}
                    <span className={cx('rounded px-2 py-0.5 text-xs', unset
                        ? 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'
                        : 'bg-gray-100 font-mono text-gray-700 dark:bg-gray-800 dark:text-gray-300')}>
                        {unset ? unsetLabel : format(value)}
                    </span>
                </span>
            </div>
            <input
                id={id}
                type="range"
                min={min}
                max={max}
                step={step}
                value={unset ? (unsetAt ?? min) : value}
                onChange={(e) => onChange(Number(e.target.value))}
                className={cx('h-2 w-full cursor-pointer appearance-none rounded-lg bg-gray-200 dark:bg-gray-700', unset ? 'accent-gray-400 opacity-70' : 'accent-indigo-600')}
            />
            {hint && <p className="text-xs text-gray-500 dark:text-gray-400">{hint}</p>}
        </div>
    );
}

export function FieldGrid({ children, cols = 2 }) {
    return <div className={cx('grid grid-cols-1 gap-4', cols === 3 ? 'md:grid-cols-3' : cols === 2 ? 'md:grid-cols-2' : '')}>{children}</div>;
}

export function Divider() {
    return <hr className="border-gray-100 dark:border-gray-750" />;
}
