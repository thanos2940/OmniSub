import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { CheckCircle2, Circle, PartyPopper } from 'lucide-react';
import { api } from '../../api';

const ChecklistRow = ({ done, label }) => (
    <div className="flex items-center gap-2 text-sm">
        {done ? <CheckCircle2 size={16} className="text-emerald-500" /> : <Circle size={16} className="text-gray-300 dark:text-gray-600" />}
        <span className={done ? 'text-gray-700 dark:text-gray-300' : 'text-gray-400 dark:text-gray-500'}>{label}</span>
    </div>
);

const StepFinish = ({ onBack, onFinish }) => {
    const navigate = useNavigate();
    const [status, setStatus] = useState(null);
    const [agentCli, setAgentCli] = useState(false);

    useEffect(() => {
        api.getSetupStatus().then(res => setStatus(res.data)).catch(() => setStatus({}));
        api.getAgentRunner()
            .then(res => setAgentCli(Object.values(res.data?.engines || {}).some(Boolean)))
            .catch(() => setAgentCli(false));
    }, []);

    const handlePrimaryAction = () => {
        onFinish();
        navigate('/');
    };

    return (
        <div className="space-y-6 text-center">
            <div className="mx-auto w-14 h-14 rounded-2xl bg-emerald-500 flex items-center justify-center">
                <PartyPopper className="text-white" size={26} />
            </div>
            <div>
                <h2 className="text-lg font-bold text-gray-900 dark:text-white">You’re all set</h2>
                <p className="text-sm text-gray-500 dark:text-gray-400">Here’s what got configured — everything is editable later in Settings.</p>
            </div>

            {status && (
                <div className="text-left inline-block space-y-1.5 bg-gray-50 dark:bg-gray-900/40 rounded-lg p-4">
                    <ChecklistRow done={agentCli} label="Agent CLI found (agy, Claude Code or Gemini CLI)" />
                    <ChecklistRow done={status.has_gemini_key} label="Gemini API key, for lines the CLI refuses (optional)" />
                    <ChecklistRow done={status.auth_configured} label="Server secured" />
                    <ChecklistRow done={status.arr_configured} label="Sonarr/Radarr connected" />
                </div>
            )}

            <div className="flex items-center justify-between pt-2">
                <button onClick={onBack} disabled={!onBack} className="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 disabled:opacity-0">Back</button>
                <button onClick={handlePrimaryAction} className="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm font-medium">
                    {status?.arr_configured ? 'Open the library' : 'Create your first project'}
                </button>
            </div>
        </div>
    );
};

export default StepFinish;
