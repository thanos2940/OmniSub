import { STATE_LABEL, STATE_STYLE } from './autopilotMeta';

const StateChip = ({ state }) => (
    <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold border whitespace-nowrap ${STATE_STYLE[state] || STATE_STYLE.skipped}`}>
        {STATE_LABEL[state] || state}
    </span>
);

export default StateChip;
