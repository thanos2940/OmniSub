import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../../api';
import { isMissingEndpoint, errorText } from './autopilotMeta';

// Polls GET /api/autopilot/status while the tab is visible and stops on unmount.
// `unsupported` = the backend has no autopilot routes (404): older OmbiSub.
export default function useAutopilotStatus(intervalMs = 5000) {
    const [status, setStatus] = useState(null);
    const [unsupported, setUnsupported] = useState(false);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);
    const alive = useRef(true);

    const refresh = useCallback(async () => {
        try {
            const res = await api.getAutopilotStatus();
            if (!alive.current) return;
            setStatus(res.data || {});
            setUnsupported(false);
            setError('');
        } catch (e) {
            if (!alive.current) return;
            if (isMissingEndpoint(e)) setUnsupported(true);
            else setError(errorText(e));
        } finally {
            if (alive.current) setLoading(false);
        }
    }, []);

    useEffect(() => {
        alive.current = true;
        refresh();
        const t = setInterval(() => { if (!document.hidden) refresh(); }, intervalMs);
        const onVis = () => { if (!document.hidden) refresh(); };
        document.addEventListener('visibilitychange', onVis);
        return () => {
            alive.current = false;
            clearInterval(t);
            document.removeEventListener('visibilitychange', onVis);
        };
    }, [refresh, intervalMs]);

    return { status, unsupported, error, loading, refresh, setStatus };
}
