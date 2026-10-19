// Small formatters for the runner panel.

export const fmtTime = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' });
};

// Minutes -> "~45 min" / "~2 h 10 min" / "~2 d 3 h"; null while there is no estimate.
export const fmtMinutes = (minutes) => {
    if (minutes == null || !Number.isFinite(Number(minutes)) || Number(minutes) < 0) return null;
    const total = Math.round(Number(minutes));
    if (total < 1) return '< 1 min';
    if (total < 60) return `~${total} min`;
    const hours = Math.floor(total / 60);
    if (hours >= 48) {
        const days = Math.floor(hours / 24);
        return hours % 24 ? `~${days} d ${hours % 24} h` : `~${days} d`;
    }
    return total % 60 ? `~${hours} h ${total % 60} min` : `~${hours} h`;
};

// Lines per minute with one decimal below 10 ("2.5"), whole numbers above; null when there is no speed yet.
export const fmtRate = (linesPerMin) => {
    const v = Number(linesPerMin);
    if (!Number.isFinite(v) || v <= 0) return null;
    return v >= 10 ? String(Math.round(v)) : v.toFixed(1);
};
