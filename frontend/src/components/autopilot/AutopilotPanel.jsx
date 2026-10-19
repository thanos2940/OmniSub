import { useState } from 'react';
import { useToast } from '../../context/ToastContext';
import { Card, Spinner } from '../ui';
import useAutopilotStatus from './useAutopilotStatus';
import StatusHeader from './StatusHeader';
import Counters from './Counters';
import ItemsTable from './ItemsTable';
import UpdateNotice from './UpdateNotice';

// The Autopilot tab of the Activity page.
const AutopilotPanel = () => {
    const toast = useToast();
    const { status, unsupported, error, loading, refresh } = useAutopilotStatus(5000);
    const [state, setState] = useState('');

    if (loading && !status && !unsupported) return <Spinner label="Loading Autopilot..." />;
    if (unsupported) return <UpdateNotice />;
    if (!status) {
        return (
            <Card>
                <p className="text-sm text-rose-700 dark:text-rose-300">
                    Could not load the Autopilot status{error ? `: ${error}` : '.'} It retries every few seconds.
                </p>
            </Card>
        );
    }

    return (
        <div className="space-y-5">
            {error && (
                <p className="rounded-lg bg-amber-50 p-2.5 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
                    Lost contact with the server ({error}). Showing the last known status.
                </p>
            )}
            <StatusHeader status={status} toast={toast} onChanged={refresh} />
            <Counters counts={status.counts || {}} active={state} onPick={setState} />
            <ItemsTable state={state} onStateChange={setState} toast={toast} onChanged={refresh} />
        </div>
    );
};

export default AutopilotPanel;
