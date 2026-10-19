import { StatusChip } from '../ui';
import { STATE_LABEL, STATE_TONE } from './autopilotMeta';

const StateChip = ({ state }) => (
    <StatusChip tone={STATE_TONE[state] || 'neutral'} className="whitespace-nowrap">{STATE_LABEL[state] || state}</StatusChip>
);

export default StateChip;
