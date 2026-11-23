import { LANGUAGES } from '../../utils/languages';
import { INPUT } from '../ui';

// A target-language picker. Stores the English name ("Greek"), as the free-text field did; a stored value
// that is not in the list (typed before this picker existed) stays selectable.
export default function LanguageSelect({ id, value, onChange, placeholder = 'Choose a language', className, autoFocus }) {
    const known = LANGUAGES.some(l => l.name === value);
    return (
        <select
            id={id}
            className={className || INPUT}
            value={value || ''}
            onChange={(e) => onChange(e.target.value)}
            autoFocus={autoFocus}
        >
            {!value && <option value="" disabled>{placeholder}</option>}
            {value && !known && <option value={value}>{value}</option>}
            {LANGUAGES.map(l => (
                <option key={l.code} value={l.name}>
                    {l.native && l.native !== l.name ? `${l.name} — ${l.native}` : l.name}
                </option>
            ))}
        </select>
    );
}
