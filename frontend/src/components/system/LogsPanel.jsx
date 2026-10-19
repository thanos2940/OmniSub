import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Download, RefreshCw, ScrollText, Search } from 'lucide-react';
import { api } from '../../api';
import { useToast } from '../../context/ToastContext';
import { Button, Card, EmptyState, Field, INPUT, Spinner, Toggle } from '../ui';
import { formatBytes } from './systemFormat';

const LOG_TITLES = {
    ombisub: 'OmbiSub (server)',
    agent_runner: 'Translation runner',
    tray_app: 'Supervisor',
    service_console: 'Service console',
};
const LINE_OPTIONS = [200, 500, 2000];
const REFRESH_MS = 5000;

const lineClass = (line) => {
    if (/\b(ERROR|CRITICAL|Traceback|Exception)\b/.test(line)) return 'text-rose-600 dark:text-rose-400';
    if (/\bWARN(ING)?\b/.test(line)) return 'text-amber-600 dark:text-amber-400';
    return 'text-gray-800 dark:text-gray-200';
};

export default function LogsPanel() {
    const toast = useToast();
    const [files, setFiles] = useState(null);
    const [name, setName] = useState('');
    const [lines, setLines] = useState(500);
    const [data, setData] = useState(null);
    const [missing, setMissing] = useState(false);
    const [loading, setLoading] = useState(false);
    const [auto, setAuto] = useState(false);
    const [wrap, setWrap] = useState(true);
    const [filter, setFilter] = useState('');
    const box = useRef(null);
    const stick = useRef(true);
    const alive = useRef(true);

    useEffect(() => {
        alive.current = true;
        api.getSystemLogList()
            .then(res => {
                if (!alive.current) return;
                const list = res.data?.logs || [];
                setFiles(list);
                setName(list.find(l => l.name === 'ombisub')?.name || list[0]?.name || '');
            })
            .catch(err => {
                if (alive.current) {
                    setFiles([]);
                    toast.error(`Could not list the log files: ${err.response?.data?.detail || err.message}`);
                }
            });
        return () => { alive.current = false; };
    }, [toast]);

    const load = useCallback(async () => {
        if (!name) return;
        setLoading(true);
        try {
            const res = await api.getSystemLog(name, lines);
            if (!alive.current) return;
            setData(res.data);
            setMissing(false);
        } catch (err) {
            if (!alive.current) return;
            if (err.response?.status === 404) {
                setData(null);
                setMissing(true);
            } else {
                toast.error(`Could not read the log: ${err.response?.data?.detail || err.message}`);
            }
        } finally {
            if (alive.current) setLoading(false);
        }
    }, [name, lines, toast]);

    useEffect(() => {
        stick.current = true;
        load();
    }, [load]);

    useEffect(() => {
        if (!auto) return undefined;
        const t = setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
        return () => clearInterval(t);
    }, [auto, load]);

    const shown = useMemo(() => {
        const all = data?.lines || [];
        const q = filter.trim().toLowerCase();
        return q ? all.filter(l => l.toLowerCase().includes(q)) : all;
    }, [data, filter]);

    // Newest lines are at the bottom; follow them while the reader is at the bottom.
    useEffect(() => {
        const el = box.current;
        if (el && stick.current) el.scrollTop = el.scrollHeight;
    }, [shown]);

    const onScroll = () => {
        const el = box.current;
        if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    };

    const download = () => {
        const blob = new Blob([(data?.lines || []).join('\n') + '\n'], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `${name}.txt`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
    };

    if (!files) return <Spinner label="Looking for log files..." />;
    if (files.length === 0) {
        return <Card padded={false}><EmptyState icon={ScrollText} title="No log files yet" description="OmbiSub writes its logs to the logs folder inside the data folder. They appear after the first start." /></Card>;
    }

    const current = files.find(f => f.name === name);

    return (
        <Card
            title={LOG_TITLES[name] || name}
            subtitle={current ? `${formatBytes(current.size)} · changed ${new Date(data?.modified || current.modified).toLocaleString()}` : undefined}
            actions={(
                <>
                    <Button size="sm" icon={RefreshCw} loading={loading} onClick={load}>Refresh</Button>
                    <Button size="sm" icon={Download} disabled={!data?.lines?.length} onClick={download}>Download</Button>
                </>
            )}
        >
            <div className="space-y-4">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                    <Field label="Log" htmlFor="log-name">
                        <select id="log-name" className={INPUT} value={name} onChange={e => setName(e.target.value)}>
                            {files.map(f => <option key={f.name} value={f.name}>{LOG_TITLES[f.name] || f.name}</option>)}
                        </select>
                    </Field>
                    <Field label="Last lines" htmlFor="log-lines">
                        <select id="log-lines" className={INPUT} value={lines} onChange={e => setLines(Number(e.target.value))}>
                            {LINE_OPTIONS.map(n => <option key={n} value={n}>{n}</option>)}
                        </select>
                    </Field>
                    <Field label="Filter" htmlFor="log-filter" className="sm:col-span-2">
                        <div className="relative">
                            <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                            <input id="log-filter" type="search" className={`${INPUT} pl-8`} placeholder="Show only lines containing..." value={filter} onChange={e => setFilter(e.target.value)} />
                        </div>
                    </Field>
                </div>
                <div className="flex flex-wrap gap-x-8 gap-y-3">
                    <Toggle checked={auto} onChange={setAuto} label="Refresh every 5 seconds" />
                    <Toggle checked={wrap} onChange={setWrap} label="Wrap long lines" />
                </div>

                {missing ? (
                    <p className="rounded-lg bg-gray-50 p-4 text-sm text-gray-600 dark:bg-gray-900 dark:text-gray-300">This log has not been written yet.</p>
                ) : (
                    <div
                        ref={box}
                        onScroll={onScroll}
                        className="h-[60vh] select-text overflow-auto rounded-lg border border-gray-200 bg-gray-50 p-3 font-mono text-xs leading-relaxed dark:border-gray-750 dark:bg-gray-950"
                        role="log"
                        aria-label="Log lines"
                    >
                        {shown.length === 0 ? (
                            <span className="text-gray-500">{data ? 'No line matches the filter.' : 'Loading...'}</span>
                        ) : shown.map((line, i) => (
                            <div key={i} className={`${wrap ? 'whitespace-pre-wrap break-words' : 'whitespace-pre'} ${lineClass(line)}`}>{line}</div>
                        ))}
                    </div>
                )}
                {data && <p className="text-xs text-gray-500 dark:text-gray-400">Showing {shown.length} of the last {data.lines.length} lines.</p>}
            </div>
        </Card>
    );
}
