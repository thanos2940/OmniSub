// Dark mode is Tailwind's `dark` class on <html> (darkMode: 'class'). index.html sets it before the first
// paint, so a reload never flashes the other theme; this module keeps it following the system setting
// until a theme is chosen in the command palette, and applies that choice.

const KEY = 'theme';

function storedTheme() {
    try {
        const theme = localStorage.getItem(KEY);
        return theme === 'dark' || theme === 'light' ? theme : null;
    } catch {
        return null; // storage blocked (private window): follow the system
    }
}

function systemDark() {
    return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-color-scheme: dark)').matches;
}

// Puts the class on <html> for the chosen theme (or the system's). Returns true when dark.
export function applyTheme() {
    const theme = storedTheme();
    const dark = theme ? theme === 'dark' : systemDark();
    document.documentElement.classList.toggle('dark', dark);
    return dark;
}

// theme: 'dark' | 'light', or null to follow the system again. Returns true when dark.
export function setTheme(theme) {
    try {
        if (theme) localStorage.setItem(KEY, theme);
        else localStorage.removeItem(KEY);
    } catch {
        // not remembered, but still applied below
    }
    return applyTheme();
}

// While no theme is chosen, a change of the system setting is followed at once. Returns the unsubscribe.
export function followSystemTheme() {
    if (typeof window.matchMedia !== 'function') return () => {};
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => {
        if (!storedTheme()) applyTheme();
    };
    query.addEventListener?.('change', onChange);
    return () => query.removeEventListener?.('change', onChange);
}
