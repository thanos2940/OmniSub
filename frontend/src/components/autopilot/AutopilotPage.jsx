import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import useAutopilotStatus from './useAutopilotStatus';
import StatusHeader from './StatusHeader';
import Counters from './Counters';
import ItemsTable from './ItemsTable';
import UpdateNotice from './UpdateNotice';

const AutopilotPage = () => {
    const toast = useToast();
    const { status, unsupported, error, loading, refresh } = useAutopilotStatus(5000);
    const [state, setState] = useState('');

    return (
        <div className="max-w-6xl mx-auto p-6 md:p-8 space-y-5">
            <header>
                <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Autopilot</h1>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                    New episodes and movies get Greek subtitles by themselves, five minutes after Sonarr or Radarr imports them.
                </p>
            </header>

            {loading && !status && !unsupported ? (
                <div className="flex justify-center py-16 text-gray-400"><Loader2 className="animate-spin" size={24} /></div>
            ) : unsupported ? (
                <UpdateNotice />
            ) : !status ? (
                <div className="p-5 rounded-2xl border border-rose-300/60 dark:border-rose-800/50 bg-rose-50 dark:bg-rose-950/20 text-sm text-rose-700 dark:text-rose-300">
                    Could not load the Autopilot status{error ? `: ${error}` : '.'} It retries every few seconds.
                </div>
            ) : (
                <>
                    {error && (
                        <div className="text-xs rounded-xl border border-amber-200 dark:border-amber-800/50 bg-amber-50 dark:bg-amber-950/20 text-amber-800 dark:text-amber-300 p-2.5">
                            Lost contact with the server ({error}). Showing the last known status.
                        </div>
                    )}
                    <StatusHeader status={status} toast={toast} onChanged={refresh} />
                    <Counters counts={status.counts || {}} active={state} onPick={setState} />
                    <ItemsTable state={state} onStateChange={setState} toast={toast} onChanged={refresh} />
                </>
            )}
        </div>
    );
};

export default AutopilotPage;
