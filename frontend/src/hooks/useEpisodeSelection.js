import { useState, useCallback, useRef } from 'react';

export const useEpisodeSelection = (episodes = []) => {
    const [selectedEpisodes, setSelectedEpisodes] = useState(new Set());
    const lastSelectedEpRef = useRef(null);

    const toggleEpisode = useCallback((epName, isShift = false, currentList = episodes) => {
        if (!epName || !String(epName).trim()) return;
        const cleanName = String(epName).trim();

        setSelectedEpisodes(prev => {
            const next = new Set(prev);

            if (isShift && lastSelectedEpRef.current && currentList.length > 0) {
                const lastIdx = currentList.findIndex(e => e.name === lastSelectedEpRef.current);
                const currIdx = currentList.findIndex(e => e.name === cleanName);
                if (lastIdx !== -1 && currIdx !== -1) {
                    const [start, end] = lastIdx < currIdx ? [lastIdx, currIdx] : [currIdx, lastIdx];
                    for (let i = start; i <= end; i++) {
                        if (currentList[i]?.name) {
                            next.add(String(currentList[i].name).trim());
                        }
                    }
                    return next;
                }
            }

            if (next.has(cleanName)) {
                next.delete(cleanName);
            } else {
                next.add(cleanName);
            }
            lastSelectedEpRef.current = cleanName;
            return next;
        });
    }, [episodes]);

    const toggleMultiple = useCallback((epNames, shouldSelect) => {
        if (!epNames || epNames.length === 0) return;
        const validNames = epNames.map(n => String(n || '').trim()).filter(Boolean);
        if (validNames.length === 0) return;

        setSelectedEpisodes(prev => {
            const next = new Set(prev);
            const isAllSelected = shouldSelect !== undefined 
                ? !shouldSelect 
                : validNames.every(n => next.has(n));

            if (isAllSelected) {
                validNames.forEach(n => next.delete(n));
            } else {
                validNames.forEach(n => next.add(n));
            }
            return next;
        });
    }, []);

    const selectAll = useCallback((currentList = episodes) => {
        const names = (currentList || []).map(e => String(e.name || '').trim()).filter(Boolean);
        setSelectedEpisodes(new Set(names));
    }, [episodes]);

    const clearSelection = useCallback(() => {
        setSelectedEpisodes(new Set());
        lastSelectedEpRef.current = null;
    }, []);

    const invertSelection = useCallback((currentList = episodes) => {
        setSelectedEpisodes(prev => {
            const next = new Set();
            (currentList || []).forEach(e => {
                const name = String(e.name || '').trim();
                if (name && !prev.has(name)) next.add(name);
            });
            return next;
        });
    }, [episodes]);

    return {
        selectedEpisodes,
        setSelectedEpisodes,
        toggleEpisode,
        toggleMultiple,
        selectAll,
        clearSelection,
        invertSelection,
    };
};

