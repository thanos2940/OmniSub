import { Copy } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { Button, Modal } from '../ui';

const lineClass = (line) => {
    const l = line.toLowerCase();
    if (/(failed|failure|error|exception)/.test(l)) return 'text-rose-400';
    if (/(warning|incomplete|limit)/.test(l)) return 'text-amber-400';
    if (/(export|saved|stored|checkpoint file)/.test(l)) return 'text-emerald-400';
    return 'text-gray-300';
};

// The execution log of one queue item.
export default function QueueLogsModal({ logs, onClose }) {
    const toast = useToast();
    const lines = logs?.lines || [];
    const copy = async () => {
        try {
            await navigator.clipboard.writeText(lines.join('\n'));
            toast.info('Log copied.');
        } catch {
            toast.error('Could not copy the log.');
        }
    };
    return (
        <Modal
            open={!!logs}
            title={`Log: ${logs?.title || ''}`}
            onClose={onClose}
            width="xl"
            footer={(
                <>
                    <Button icon={Copy} onClick={copy} disabled={!lines.length}>Copy</Button>
                    <Button variant="primary" onClick={onClose}>Close</Button>
                </>
            )}
        >
            {lines.length === 0 ? (
                <p className="text-sm text-gray-500 dark:text-gray-400">Nothing was logged for this item.</p>
            ) : (
                <div className="max-h-[60vh] select-text space-y-0.5 overflow-y-auto rounded-lg bg-gray-950 p-3 font-mono text-xs leading-relaxed">
                    {lines.map((line, i) => <div key={i} className={`whitespace-pre-wrap break-words ${lineClass(line)}`}>{line}</div>)}
                </div>
            )}
        </Modal>
    );
}
