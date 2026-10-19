import React from 'react';
import { ArrowDown, Clock, EyeOff, Flame } from 'lucide-react';

const TIERS = [
    { id: 'high', label: 'High', title: 'High: translated first', icon: Flame, active: 'bg-amber-500 text-white' },
    { id: 'normal', label: 'Normal', title: 'Normal', icon: Clock, active: 'bg-indigo-600 text-white' },
    { id: 'low', label: 'Low', title: 'Low: translated last', icon: ArrowDown, active: 'bg-sky-600 text-white' },
    { id: 'ignored', label: 'Ignored', title: 'Ignored: no automatic translation', icon: EyeOff, active: 'bg-rose-600 text-white' },
];

// The four priority tiers of a title as one segmented control.
const PriorityControl = ({ value, onChange, disabled }) => (
    <div role="radiogroup" aria-label="Translation priority" className="inline-flex flex-wrap gap-0.5 rounded-lg border border-gray-200 bg-gray-50 p-0.5 dark:border-gray-700 dark:bg-gray-900/50">
        {TIERS.map(({ id, label, title, icon: Icon, active }) => {
            const on = value === id;
            return (
                <button key={id} type="button" role="radio" aria-checked={on} title={title} disabled={disabled} onClick={() => !on && onChange(id)}
                    className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-60 ${on
                        ? active
                        : 'text-gray-600 hover:bg-white hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-800 dark:hover:text-white'}`}>
                    <Icon size={13} /> {label}
                </button>
            );
        })}
    </div>
);

export default PriorityControl;
