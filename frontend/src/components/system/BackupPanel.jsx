import { useState, useEffect, useCallback } from 'react';
import { Archive, CheckCircle2, Download, Upload } from 'lucide-react';
import { api } from '../../api';
import { useToast } from '../../context/ToastContext';
import { Button, Card, ConfirmDialog, Spinner, StatusChip } from '../ui';

const MODES = [
    {
        id: 'full',
        title: 'Full bundle',
        note: 'Use this to move the server. Everything in the compact backup plus the source subtitle each episode was translated from, which re-exporting ASS typesetting and retranslating need. No video files.',
        sizeKey: 'full_size_human',
    },
    {
        id: 'compact',
        title: 'Compact backup',
        note: 'Settings, databases, glossaries, translation memory and every translation, without the source subtitle files. A backup of your work, not a server move: ASS episodes cannot be re-exported or retranslated from it.',
        sizeKey: 'compact_size_human',
    },
];

// Export a bundle of the server's data, or restore one on a new server.
export default function BackupPanel() {
    const toast = useToast();
    const [estimate, setEstimate] = useState(null);
    const [loading, setLoading] = useState(true);
    const [mode, setMode] = useState('full');
    const [exporting, setExporting] = useState(false);
    const [file, setFile] = useState(null);
    const [inspecting, setInspecting] = useState(false);
    const [inspect, setInspect] = useState(null);
    const [convertUnc, setConvertUnc] = useState(true);
    const [confirmRestore, setConfirmRestore] = useState(false);
    const [importing, setImporting] = useState(false);
    const [imported, setImported] = useState(null);

    const loadEstimate = useCallback(async () => {
        try {
            const res = await api.getBackupEstimate();
            setEstimate(res.data);
        } catch (err) {
            console.error('Failed to fetch backup estimate:', err);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { loadEstimate(); }, [loadEstimate]);

    const exportBundle = async () => {
        setExporting(true);
        try {
            const href = await api.backupDownloadLink(mode);
            const link = document.createElement('a');
            link.href = href;
            link.setAttribute('download', '');
            document.body.appendChild(link);
            link.click();
            link.remove();
            toast.success('Building the bundle. Your browser starts the download when it is ready; a large library takes a few minutes.');
        } catch (err) {
            toast.error(`Could not export the bundle: ${err.response?.data?.detail || err.message}`);
        } finally {
            setExporting(false);
        }
    };

    const pickFile = async (e) => {
        const picked = e.target.files?.[0];
        if (!picked) return;
        setFile(picked);
        setInspecting(true);
        setInspect(null);
        setImported(null);
        try {
            const form = new FormData();
            form.append('file', picked);
            const res = await api.inspectBackupBundle(form);
            setInspect(res.data);
        } catch (err) {
            toast.error(err.response?.data?.detail || 'The file is not a valid OmbiSub bundle.');
            setFile(null);
        } finally {
            setInspecting(false);
        }
    };

    const restore = async () => {
        if (!file) return;
        setImporting(true);
        try {
            const form = new FormData();
            form.append('file', file);
            form.append('remap_unc_to_local', convertUnc);
            const res = await api.importBackupBundle(form);
            setImported(res.data);
            toast.success(res.data.message || 'The bundle was restored.');
            loadEstimate();
        } catch (err) {
            toast.error(err.response?.data?.detail || 'Could not restore the bundle.');
        } finally {
            setImporting(false);
            setConfirmRestore(false);
        }
    };

    if (loading) return <Spinner label="Reading the library size..." />;

    return (
        <div className="space-y-5">
            <Card
                title="Export a bundle"
                subtitle={estimate ? `${estimate.hostname} · ${estimate.project_count} projects · ${estimate.episode_count} episodes` : undefined}
                actions={<Button variant="primary" icon={Download} loading={exporting} onClick={exportBundle}>Export {mode === 'full' ? 'full bundle' : 'compact backup'}</Button>}
            >
                <div role="radiogroup" aria-label="Bundle type" className="grid grid-cols-1 gap-3 md:grid-cols-2">
                    {MODES.map(m => (
                        <button
                            key={m.id}
                            type="button"
                            role="radio"
                            aria-checked={mode === m.id}
                            onClick={() => setMode(m.id)}
                            className={`rounded-lg border p-4 text-left transition-colors ${mode === m.id
                                ? 'border-indigo-500 bg-indigo-50/60 ring-1 ring-indigo-500 dark:bg-indigo-950/20'
                                : 'border-gray-200 hover:border-gray-300 dark:border-gray-700 dark:hover:border-gray-600'}`}
                        >
                            <div className="flex items-center justify-between gap-2">
                                <span className="text-sm font-semibold text-gray-900 dark:text-white">{m.title}</span>
                                <span className="font-mono text-xs text-gray-500 dark:text-gray-400">~{estimate?.[m.sizeKey] || '?'}</span>
                            </div>
                            <p className="mt-1 text-xs leading-relaxed text-gray-600 dark:text-gray-400">{m.note}</p>
                        </button>
                    ))}
                </div>
            </Card>

            <Card title="Restore a bundle" subtitle="Replaces this server's projects, databases and settings with the ones in the bundle.">
                <div className="space-y-4">
                    <label htmlFor="backup-file-input" className="block cursor-pointer rounded-lg border-2 border-dashed border-gray-300 p-6 text-center transition-colors hover:border-indigo-400 dark:border-gray-700 dark:hover:border-indigo-500">
                        <input id="backup-file-input" type="file" accept=".zip" onChange={pickFile} className="sr-only" />
                        <Archive size={28} className="mx-auto text-gray-400" />
                        <span className="mt-2 block text-sm font-medium text-gray-700 dark:text-gray-300">{file ? file.name : 'Choose an OmbiSub bundle (.zip)'}</span>
                        <span className="block text-xs text-gray-500 dark:text-gray-400">Full and compact bundles both work.</span>
                    </label>

                    {inspecting && <Spinner label="Checking the bundle..." className="py-4" />}

                    {inspect && (
                        <div className="space-y-3 rounded-lg border border-gray-200 p-4 dark:border-gray-750">
                            <div className="flex flex-wrap items-center gap-2">
                                <StatusChip tone="success" icon={CheckCircle2}>Bundle verified</StatusChip>
                                <span className="break-all font-mono text-xs text-gray-500 dark:text-gray-400">SHA-256 {inspect.sha256}</span>
                            </div>
                            <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                                <div><dt className="text-xs text-gray-500">From</dt><dd className="font-mono">{inspect.hostname}</dd></div>
                                <div><dt className="text-xs text-gray-500">Projects</dt><dd>{inspect.project_count}</dd></div>
                                <div><dt className="text-xs text-gray-500">Episodes</dt><dd>{inspect.episode_count}</dd></div>
                                <div><dt className="text-xs text-gray-500">Kind</dt><dd className="capitalize">{inspect.mode}</dd></div>
                            </dl>
                            <label className="flex items-start gap-2 text-sm text-gray-700 dark:text-gray-300">
                                <input type="checkbox" checked={convertUnc} onChange={e => setConvertUnc(e.target.checked)} className="mt-0.5 h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500" />
                                <span>Turn network paths such as <code className="font-mono text-xs">\\server\e\...</code> into local drive paths such as <code className="font-mono text-xs">E:\...</code></span>
                            </label>
                            <div className="flex justify-end">
                                <Button variant="primary" icon={Upload} onClick={() => setConfirmRestore(true)}>Restore this bundle</Button>
                            </div>
                        </div>
                    )}

                    {imported && (
                        <p className="flex items-start gap-2 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-300">
                            <CheckCircle2 size={16} className="mt-0.5 shrink-0" />
                            {imported.message} ({imported.restored_projects} projects, {imported.restored_episodes} episodes).
                        </p>
                    )}
                </div>
            </Card>

            <ConfirmDialog
                open={confirmRestore}
                title="Restore this bundle?"
                message="The projects, databases, translation memory and settings on this server are replaced with the bundle's. A copy of each replaced database is kept next to it as .pre_import.bak."
                confirmLabel="Restore"
                danger
                busy={importing}
                onConfirm={restore}
                onCancel={() => setConfirmRestore(false)}
            />
        </div>
    );
}
