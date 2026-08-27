import React, { useState } from 'react';
import { Gauge, Scale, Trophy } from 'lucide-react';
import { api } from '../../api';
import { QUALITY_PRESETS } from '../settings/presets';

const ICONS = { fast: Gauge, balanced: Scale, best: Trophy };

const StepQuality = ({ onNext, onBack, onSkip }) => {
    const [selected, setSelected] = useState('balanced');
    const [saving, setSaving] = useState(false);

    const handleNext = async () => {
        setSaving(true);
        try {
            await api.updateSettings(QUALITY_PRESETS[selected].values);
        } catch (e) {
            console.error('Failed to save quality preset', e);
        } finally {
            setSaving(false);
            onNext();
        }
    };

    return (
        <div className="space-y-5">
            <div>
                <h2 className="text-lg font-bold text-gray-900 dark:text-white">Translation quality</h2>
                <p className="text-sm text-gray-500 dark:text-gray-400">A starting point — change this anytime in Settings.</p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {Object.entries(QUALITY_PRESETS).map(([key, preset]) => {
                    const Icon = ICONS[key];
                    return (
                        <button
                            key={key}
                            onClick={() => setSelected(key)}
                            className={`p-4 rounded-xl border text-left transition-all ${selected === key
                                ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-900/20 ring-1 ring-indigo-500'
                                : 'border-gray-200 dark:border-gray-600 hover:border-gray-300'}`}
                        >
                            <Icon size={20} className={selected === key ? 'text-indigo-600' : 'text-gray-400'} />
                            <p className="text-sm font-semibold mt-2 text-gray-900 dark:text-white">{preset.label}</p>
                            <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1 leading-tight">{preset.desc}</p>
                        </button>
                    );
                })}
            </div>

            <div className="flex items-center justify-between pt-2">
                <button onClick={onBack} disabled={!onBack} className="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 disabled:opacity-0">Back</button>
                <div className="flex gap-3">
                    <button onClick={onSkip} className="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">Skip</button>
                    <button onClick={handleNext} disabled={saving} className="px-5 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm font-medium disabled:opacity-50">
                        {saving ? 'Saving...' : 'Continue'}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default StepQuality;
