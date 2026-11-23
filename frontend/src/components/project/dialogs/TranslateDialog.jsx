import { useEffect, useState } from 'react';
import { Languages } from 'lucide-react';
import { Button, Modal } from '../../ui';

const OPTION = 'flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm transition-colors';
const ON = 'border-indigo-500 bg-indigo-50/60 dark:border-indigo-400 dark:bg-indigo-950/30';
const OFF = 'border-gray-200 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800';

// One place to say how selected episodes are translated: only what is missing, or everything again, and whether
// the subtitle file next to the video may be replaced even when Omnisub did not write it.
export default function TranslateDialog({ open, names = [], defaultMode = 'gaps', onCancel, onConfirm }) {
    const [mode, setMode] = useState(defaultMode);
    const [overwrite, setOverwrite] = useState(false);
    useEffect(() => {
        if (open) { setMode(defaultMode); setOverwrite(false); }
    }, [open, defaultMode]);

    const count = names.length;
    const what = count === 1 ? names[0] : `${count} episodes`;
    return (
        <Modal
            open={open}
            title={`Translate ${what}`}
            onClose={onCancel}
            footer={(
                <>
                    <Button variant="ghost" onClick={onCancel}>Cancel</Button>
                    <Button variant="primary" icon={Languages} onClick={() => onConfirm({ full: mode === 'full', overwrite })}>
                        {mode === 'full' ? 'Translate again' : 'Translate'}
                    </Button>
                </>
            )}
        >
            <div className="space-y-4">
                <div role="radiogroup" aria-label="What to translate" className="space-y-2">
                    <label className={`${OPTION} ${mode === 'gaps' ? ON : OFF}`}>
                        <input type="radio" name="translate-mode" className="mt-0.5" checked={mode === 'gaps'} onChange={() => setMode('gaps')} />
                        <span>
                            <span className="block font-medium text-gray-900 dark:text-white">Only what is missing</span>
                            <span className="block text-xs text-gray-500 dark:text-gray-400">Episodes without a translation are translated; translated ones only get their missing lines.</span>
                        </span>
                    </label>
                    <label className={`${OPTION} ${mode === 'full' ? ON : OFF}`}>
                        <input type="radio" name="translate-mode" className="mt-0.5" checked={mode === 'full'} onChange={() => setMode('full')} />
                        <span>
                            <span className="block font-medium text-gray-900 dark:text-white">Everything again</span>
                            <span className="block text-xs text-gray-500 dark:text-gray-400">A new translation replaces the current one, lines you edited by hand included.</span>
                        </span>
                    </label>
                </div>
                <label className="flex cursor-pointer items-start gap-3 text-sm text-gray-700 dark:text-gray-300">
                    <input type="checkbox" className="mt-0.5 rounded border-gray-300 text-indigo-600" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
                    <span>
                        Replace the subtitle file next to the video
                        <span className="block text-xs text-gray-500 dark:text-gray-400">
                            Even one Omnisub did not write (from Bazarr or a download). Without this, such a file is left as it is and the episode is marked “not exported”.
                        </span>
                    </span>
                </label>
            </div>
        </Modal>
    );
}
