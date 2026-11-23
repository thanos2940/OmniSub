import { useEffect, useState } from 'react';
import { Film } from 'lucide-react';
import { Button, Modal } from '../../ui';

const OPTION = 'flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm transition-colors';
const ON = 'border-indigo-500 bg-indigo-50/60 dark:border-indigo-400 dark:bg-indigo-950/30';
const OFF = 'border-gray-200 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800';

const FORMATS = [
    { id: 'auto', label: 'The best for each episode', note: 'ASS when the video has it (styles, signs and positions), else SRT. A subtitle an episode already has stays, except an SRT in anime.' },
    { id: 'ass', label: 'ASS', note: 'Keeps the styling, signs and on-screen positions.' },
    { id: 'srt', label: 'SRT', note: 'Plain text: what most players and Plex show by default.' },
];

// Extract the English subtitle track of each selected episode's video into a file next to it (.ass or .srt),
// import it as the episode's source, and optionally queue the translation.
export default function ExtractDialog({ open, names = [], onCancel, onConfirm }) {
    const [format, setFormat] = useState('auto');
    const [force, setForce] = useState(false);
    const [translateAfter, setTranslateAfter] = useState(false);
    useEffect(() => {
        if (open) { setFormat('auto'); setForce(false); setTranslateAfter(false); }
    }, [open]);

    const count = names.length;
    return (
        <Modal
            open={open}
            title={`Extract subtitles from ${count === 1 ? names[0] : `${count} videos`}`}
            onClose={onCancel}
            footer={(
                <>
                    <Button variant="ghost" onClick={onCancel}>Cancel</Button>
                    <Button variant="primary" icon={Film}
                        onClick={() => onConfirm({ prefer: format === 'auto' ? null : format, force, translateAfter })}>
                        Extract
                    </Button>
                </>
            )}
        >
            <div className="space-y-4">
                <p className="text-sm text-gray-600 dark:text-gray-300">
                    The source-language track inside each video is written next to it as a subtitle file and becomes the episode’s source.
                    To pick a particular track of one episode, use “Extract subtitle from video…” in its row.
                </p>
                <div role="radiogroup" aria-label="Format" className="space-y-2">
                    {FORMATS.map(f => (
                        <label key={f.id} className={`${OPTION} ${format === f.id ? ON : OFF}`}>
                            <input type="radio" name="extract-format" className="mt-0.5" checked={format === f.id} onChange={() => setFormat(f.id)} />
                            <span>
                                <span className="block font-medium text-gray-900 dark:text-white">{f.label}</span>
                                <span className="block text-xs text-gray-500 dark:text-gray-400">{f.note}</span>
                            </span>
                        </label>
                    ))}
                </div>
                <label className="flex cursor-pointer items-start gap-3 text-sm text-gray-700 dark:text-gray-300">
                    <input type="checkbox" className="mt-0.5 rounded border-gray-300 text-indigo-600" checked={force} onChange={(e) => setForce(e.target.checked)} />
                    <span>
                        Extract again where a subtitle already exists
                        <span className="block text-xs text-gray-500 dark:text-gray-400">Otherwise an episode that already has a subtitle in the chosen format keeps it.</span>
                    </span>
                </label>
                <label className="flex cursor-pointer items-start gap-3 text-sm text-gray-700 dark:text-gray-300">
                    <input type="checkbox" className="mt-0.5 rounded border-gray-300 text-indigo-600" checked={translateAfter} onChange={(e) => setTranslateAfter(e.target.checked)} />
                    <span>Translate each episode once its subtitle is in</span>
                </label>
            </div>
        </Modal>
    );
}
