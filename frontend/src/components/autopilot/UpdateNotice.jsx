import { AlertTriangle } from 'lucide-react';

// Shown instead of the Autopilot views when the backend answers 404 for /api/autopilot/*.
const UpdateNotice = ({ compact = false }) => (
    <div className={`flex items-start gap-3 rounded-2xl border border-amber-300/60 dark:border-amber-700/50 bg-amber-50 dark:bg-amber-950/20 text-amber-800 dark:text-amber-300 ${compact ? 'p-3 text-xs' : 'p-5 text-sm'}`}>
        <AlertTriangle size={compact ? 16 : 20} className="shrink-0 mt-0.5" />
        <div>
            <div className="font-bold">Autopilot is not available on this server yet</div>
            <p className="mt-0.5 opacity-90">
                The OmbiSub backend is older than this page. Update OmbiSub (System, Updates) and restart it to use Autopilot.
            </p>
        </div>
    </div>
);

export default UpdateNotice;
