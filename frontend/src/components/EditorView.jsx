import React, { useState, useRef, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Download, Book, X, AlertTriangle, Save, Trash2, Search, FolderOutput, ArrowUp, ArrowDown } from 'lucide-react';
import GlossaryEditor from './GlossaryEditor';
import ReviewLineResolver from './ReviewLineResolver';
import { api } from '../api';

// Memoized row: only re-renders when its own `item` (or isLoading/selected) changes. With
// stable callbacks below, editing one line no longer re-renders the whole episode — big win
// for long episodes where the old inline data.map() rebuilt every row on each keystroke.
const SubtitleRow = React.memo(function SubtitleRow({
    item, idx, isLoading, projectName, episodeName, selected,
    onChangeTranslation, onResolved, onUpdateTm, onToggleSelect, onDeleteLine,
}) {
    const selectCell = (
        <div className="p-2 pt-4 border-r border-gray-100 flex flex-col items-center gap-1.5">
            <input
                type="checkbox"
                checked={selected}
                onChange={() => onToggleSelect(item.id)}
                className="w-4 h-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
            />
            <span className="text-[10px] font-mono text-gray-300 select-none">{idx + 1}</span>
            <button
                onClick={() => onDeleteLine(idx)}
                className="opacity-0 group-hover:opacity-100 p-1 text-gray-300 hover:text-red-500 hover:bg-red-50 rounded transition-all"
                title="Delete this line permanently"
            >
                <Trash2 size={13} />
            </button>
        </div>
    );

    // ASS/SSA passthrough events (karaoke timing, vector drawings) are never translated —
    // render them read-only with a badge so they aren't mistaken for missing translations.
    const assMeta = item._ass || null;
    const isPassthrough = !!(assMeta && assMeta.translatable === false);
    const passthroughKind = isPassthrough ? (assMeta.kind || 'effect') : null;

    return (
        <div className={`hover:bg-gray-50/50 transition-colors group ${selected ? 'bg-indigo-50/40' : ''}`}>
            {item.needs_review ? (
                <div className="grid grid-cols-[44px_100px_1fr_1fr] divide-x divide-gray-100">
                    {selectCell}
                    <div className="p-4 text-xs font-mono text-gray-400 border-r border-gray-100 flex items-center">
                        {item.timecode}
                    </div>
                    <div className="col-span-2 p-4 bg-rose-50/10 dark:bg-rose-950/5 border-l-4 border-rose-500">
                        <div className="text-xs text-rose-500 font-bold mb-2 uppercase tracking-wide">
                            Needs Review — Cue {idx + 1}
                        </div>
                        <ReviewLineResolver
                            projectName={projectName}
                            episodeName={episodeName}
                            line={{ ...item, index: idx }}
                            onResolved={(lineIndex, resolvedText) => onResolved(lineIndex, resolvedText)}
                            onUpdateLineText={(lineIndex, newText) => onChangeTranslation(item.id, newText)}
                        />
                    </div>
                </div>
            ) : (
                <div className="grid grid-cols-[44px_100px_1fr_1fr] divide-x divide-gray-100">
                    {selectCell}
                    <div className="p-4 text-xs font-mono text-gray-400 border-r border-gray-100 flex items-center">
                        {item.timecode}
                    </div>
                    <div className="p-4 text-gray-700 border-r border-gray-100 leading-relaxed">
                        {item.original}
                    </div>
                    <div className={`p-0 transition-colors relative ${item.tm_user_edited ? 'bg-amber-50/30 group-hover:bg-amber-50/50' : 'bg-indigo-50/10 group-hover:bg-indigo-50/30'}`}>
                        <textarea
                            value={isPassthrough ? (item.original || '') : (item.translated || '')}
                            onChange={(e) => onChangeTranslation(item.id, e.target.value)}
                            placeholder={isPassthrough ? "Effect / karaoke — not translated" : (isLoading ? "Translating..." : "Not translated")}
                            disabled={isLoading || isPassthrough}
                            readOnly={isPassthrough}
                            title={isPassthrough ? "This ASS event (karaoke/vector effect) is passed through unchanged." : undefined}
                            className={`w-full h-full min-h-[80px] p-4 bg-transparent border-none outline-none resize-y text-gray-900 font-medium leading-relaxed focus:bg-white/50 focus:ring-2 transition-all placeholder:italic placeholder:text-gray-300 disabled:opacity-50 focus:ring-indigo-500/20 ${isPassthrough ? 'text-gray-400 italic' : ''}`}
                        />
                        {isLoading && !item.translated && (
                            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                                <span className="text-indigo-400 italic animate-pulse">Translating...</span>
                            </div>
                        )}
                        {item.from_tm && item.is_edited && (
                            <div className="absolute bottom-2 left-4 flex items-center gap-2 bg-amber-50/95 dark:bg-gray-800/95 px-2.5 py-1 rounded-lg border border-amber-200 dark:border-amber-900 shadow-md text-xs font-semibold text-amber-800 dark:text-amber-300 select-none z-10 hover:bg-amber-100/95 transition-colors">
                                <input
                                    type="checkbox"
                                    id={`update-tm-${item.id}`}
                                    checked={item.update_tm !== false}
                                    onChange={(e) => onUpdateTm(item.id, e.target.checked)}
                                    className="w-3.5 h-3.5 rounded border-amber-300 text-amber-600 focus:ring-amber-500 cursor-pointer"
                                />
                                <label htmlFor={`update-tm-${item.id}`} className="cursor-pointer">
                                    Update Translation Memory (future reuse)
                                </label>
                            </div>
                        )}
                        <div className="absolute top-2 right-2 pointer-events-none flex items-center gap-1 flex-wrap justify-end">
                            {isPassthrough && (
                                <span className="pointer-events-auto px-2 py-0.5 rounded text-[10px] font-bold bg-purple-100 text-purple-700 uppercase tracking-wider shadow-sm" title="Karaoke/effect line — passed through untranslated">
                                    {passthroughKind}
                                </span>
                            )}
                            {item.from_tm && (
                                item.tm_user_edited ? (
                                    <span className="pointer-events-auto px-2 py-0.5 rounded text-[10px] font-bold bg-amber-100 text-amber-700 uppercase tracking-wider shadow-sm" title={item.tm_origin_episode ? `Reused from your previous edits in ${item.tm_origin_episode}` : "Reused from your previous edits"}>User Edit</span>
                                ) : (
                                    <span className="pointer-events-auto px-2 py-0.5 rounded text-[10px] font-bold bg-indigo-100 text-indigo-700 uppercase tracking-wider shadow-sm" title={item.tm_origin_episode ? `Matched from Translation Memory (${item.tm_origin_episode})` : "Matched from Translation Memory"}>TM Match</span>
                                )
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
});

const EditorView = ({ data, glossary, onRetranslate, onUpdateGlossary, onDataUpdate, onSave, isLoading, project, projectName, episodeName, activeLanguage, originalFilename, onDeleteLines, onExportToPath, hasMediaPath, dirty, onUpdateTermsInScenes }) => {
    const [showGlossary, setShowGlossary] = useState(false);
    const [selected, setSelected] = useState(new Set());
    const [filterStatus, setFilterStatus] = useState('all'); // all, untranslated, review, edited
    const [searchQuery, setSearchQuery] = useState('');
    const selectAllRef = useRef(null);

    // Always-fresh ref to data so the callbacks below can stay stable (no `data` dep),
    // which is what lets SubtitleRow's React.memo skip unchanged rows.
    const dataRef = useRef(data);
    dataRef.current = data;

    const handleTranslationChange = useCallback((id, newText) => {
        if (onDataUpdate) {
            onDataUpdate(dataRef.current.map(item =>
                item.id === id ? { ...item, translated: newText, is_edited: true } : item
            ));
        }
    }, [onDataUpdate]);

    const handleResolved = useCallback((lineIndex, resolvedText) => {
        if (onDataUpdate) {
            onDataUpdate(dataRef.current.map((it, i) =>
                i === lineIndex
                    ? { ...it, translated: resolvedText, needs_review: false, review_issues: null }
                    : it
            ));
        }
    }, [onDataUpdate]);

    const handleUpdateTm = useCallback((id, checked) => {
        if (onDataUpdate) {
            onDataUpdate(dataRef.current.map(it => it.id === id ? { ...it, update_tm: checked } : it));
        }
    }, [onDataUpdate]);

    const handleToggleSelect = useCallback((id) => {
        setSelected(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id); else next.add(id);
            return next;
        });
    }, []);

    const handleDeleteLine = useCallback(async (idx) => {
        const item = dataRef.current[idx];
        if (!item) return;
        if (!window.confirm(`Delete cue ${idx + 1} permanently?\n\n"${(item.original || '').slice(0, 120)}"\n\nIt will also be removed from the exported subtitle file.`)) return;
        if (onDeleteLines) {
            await onDeleteLines([idx]);
            setSelected(new Set());
        }
    }, [onDeleteLines]);

    // Manual realignment for the whole-episode "split/shift" bug: when the model
    // splits a long cue and renumbers the tail, every following cue inherits its
    // neighbour's translation. Select the first misaligned cue and shift the
    // translated text (only) — timecodes/original stay put. Shifting is per active
    // language and is undoable by not saving (or reloading). Inclusive of the
    // selected cue so pointing at the first wrong line fixes it too.
    const handleShiftTranslations = useCallback((direction) => {
        if (selected.size !== 1 || !onDataUpdate) return;
        const anchorId = [...selected][0];
        const cur = dataRef.current;
        const anchor = cur.findIndex(it => it.id === anchorId);
        if (anchor < 0 || cur.length < 2) return;

        const n = cur.length;
        const last = n - 1;
        const up = direction === 'up';
        const cueNo = anchor + 1;
        const msg = up
            ? `Shift translations UP from cue ${cueNo}?\n\n`
              + `Each cue from ${cueNo} onward takes the next cue's translation `
              + `(cue ${cueNo} ← cue ${cueNo + 1}, and so on). Cue ${cueNo}'s current text is `
              + `discarded and the last cue (${n}) is left blank to re-translate.\n\n`
              + `Use this when a translation was duplicated/inserted above and everything below sits one cue too low.\n\n`
              + `Timecodes and original text never move. Nothing is saved until you click "Save Changes".`
            : `Shift translations DOWN from cue ${cueNo}?\n\n`
              + `Each cue from ${cueNo} onward takes the previous cue's translation `
              + `(cue ${cueNo + 1} ← cue ${cueNo}, and so on). Cue ${cueNo} is left blank to re-translate `
              + `and the last cue's translation is pushed off the end and discarded.\n\n`
              + `Use this when a translation is missing above and everything below sits one cue too high.\n\n`
              + `Timecodes and original text never move. Nothing is saved until you click "Save Changes".`;
        if (!window.confirm(msg)) return;

        const next = cur.map(it => ({ ...it }));
        const set = (i, text) => {
            next[i].translated = text;
            next[i].is_edited = true;
            // Content changed, so any stale review flag on this cue no longer applies.
            next[i].needs_review = false;
            next[i].review_issues = null;
        };

        if (up) {
            for (let i = anchor; i < last; i++) set(i, next[i + 1].translated || '');
            set(last, '');
        } else {
            for (let i = last; i > anchor; i--) set(i, next[i - 1].translated || '');
            set(anchor, '');
        }

        onDataUpdate(next);
        setSelected(new Set());
    }, [selected, onDataUpdate]);

    const handleDeleteSelected = async () => {
        const indexes = data
            .map((item, idx) => (selected.has(item.id) ? idx : -1))
            .filter(idx => idx !== -1);
        if (indexes.length === 0) return;
        if (!window.confirm(`Delete ${indexes.length} subtitle line${indexes.length !== 1 ? 's' : ''} permanently?\n\nThey will also be removed from the exported subtitle file.`)) return;
        if (onDeleteLines) {
            await onDeleteLines(indexes);
            setSelected(new Set());
        }
    };

    // Search + status filtering. Each visible row keeps its true index into `data`
    // so deletions and the review resolver still target the right line.
    const query = searchQuery.trim().toLowerCase();
    const matchesStatus = (item) => {
        if (filterStatus === 'untranslated') return !item.translated && !item.needs_review;
        if (filterStatus === 'review') return !!item.needs_review;
        if (filterStatus === 'edited') return !!(item.is_edited || item.tm_user_edited);
        return true;
    };
    const visibleRows = data
        .map((item, idx) => ({ item, idx }))
        .filter(({ item }) =>
            matchesStatus(item) &&
            (!query ||
                (item.original || '').toLowerCase().includes(query) ||
                (item.translated || '').toLowerCase().includes(query) ||
                (item.timecode || '').toLowerCase().includes(query))
        );

    const counts = {
        all: data.length,
        untranslated: data.filter(it => !it.translated && !it.needs_review).length,
        review: data.filter(it => it.needs_review).length,
        edited: data.filter(it => it.is_edited || it.tm_user_edited).length,
    };

    const allVisibleSelected = visibleRows.length > 0 && visibleRows.every(({ item }) => selected.has(item.id));
    const someVisibleSelected = visibleRows.some(({ item }) => selected.has(item.id));

    useEffect(() => {
        if (selectAllRef.current) {
            selectAllRef.current.indeterminate = someVisibleSelected && !allVisibleSelected;
        }
    }, [someVisibleSelected, allVisibleSelected]);

    const handleToggleSelectVisible = () => {
        setSelected(prev => {
            const next = new Set(prev);
            if (allVisibleSelected) visibleRows.forEach(({ item }) => next.delete(item.id));
            else visibleRows.forEach(({ item }) => next.add(item.id));
            return next;
        });
    };

    const handleDownload = () => {
        // ASS/SSA can't be rebuilt correctly in the browser (styles, karaoke and
        // vector events live in the original document). Fetch the format-correct file
        // from the server, which reconstructs via the original .ass and names it right.
        const isAss = Array.isArray(data) && data.some(it => it && it._ass);
        if (isAss) {
            api.downloadEpisode(projectName, episodeName);
            return;
        }
        let srtContent = "";
        data.forEach((item, index) => {
            const indexNum = index + 1;
            const timecode = item.timecode;
            // Use translated text if available, otherwise fallback to original or empty string
            const text = item.translated || item.original || "";

            srtContent += `${indexNum}\n${timecode}\n${text}\n\n`;
        });

        // Language code mapping
        const LANGUAGE_CODES = {
            "Greek": "el",
            "English": "en",
            "Spanish": "es",
            "French": "fr",
            "German": "de",
            "Italian": "it",
            "Portuguese": "pt",
            "Russian": "ru",
            "Japanese": "ja",
            "Korean": "ko",
            "Chinese": "zh"
        };

        // Label the file with the language actually being edited/downloaded (the
        // EpisodeView language selector), not the project's primary language — the
        // editor's `translated` field holds the active language's text.
        const targetLang = activeLanguage || project?.target_language || "English";
        const langCode = LANGUAGE_CODES[targetLang] || "en";

        // Generate filename with language code
        let filename = originalFilename || episodeName || "subtitle";
        const extensions = [".srt", ".ass", ".ssa"];
        let ext = ".srt";
        let base = filename;

        for (const e of extensions) {
            if (filename.toLowerCase().endsWith(e)) {
                ext = filename.slice(-e.length);
                base = filename.slice(0, -e.length);
                break;
            }
        }

        // Strip existing language tag (e.g. .en, .el)
        base = base.replace(/\.[a-zA-Z]{2,3}$/, "");

        filename = `${base}.${langCode}${ext}`;

        const element = document.createElement("a");
        const file = new Blob([srtContent], { type: 'text/plain' });
        element.href = URL.createObjectURL(file);
        element.download = filename;
        document.body.appendChild(element);
        element.click();
        document.body.removeChild(element);
    };

    return (
        <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="h-screen flex flex-col bg-slate-50"
        >
            {/* Header */}
            <header className="bg-white border-b border-gray-200 px-6 py-4 flex justify-between items-center shadow-sm z-10">
                <div className="flex items-center gap-4">
                    <h1 className="text-xl font-bold text-gray-800">Translation Editor</h1>
                    <button
                        onClick={() => setShowGlossary(true)}
                        className="flex items-center gap-2 px-3 py-1.5 bg-indigo-50 text-indigo-700 rounded-lg hover:bg-indigo-100 transition-colors text-sm font-medium"
                    >
                        <Book className="w-4 h-4" />
                        Glossary
                    </button>
                </div>
                <div className="flex items-center gap-3">
                    {dirty && (
                        <span className="flex items-center gap-1.5 text-xs font-semibold text-amber-600 bg-amber-50 px-2.5 py-1 rounded-full">
                            <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse" />
                            Unsaved changes
                        </span>
                    )}
                    <button
                        onClick={() => onSave()}
                        disabled={isLoading}
                        className="flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-all shadow-md shadow-emerald-500/20 hover:shadow-emerald-500/40 hover:-translate-y-0.5 active:translate-y-0"
                        title={hasMediaPath ? "Save changes (also re-exports the subtitle next to the media file)" : "Save changes"}
                    >
                        <Save className="w-4 h-4" />
                        Save Changes
                    </button>
                    {hasMediaPath && onExportToPath && (
                        <button
                            onClick={onExportToPath}
                            disabled={isLoading}
                            className="flex items-center gap-2 px-4 py-2 bg-white border border-gray-200 text-gray-700 rounded-lg hover:bg-emerald-50 hover:text-emerald-700 hover:border-emerald-200 transition-colors text-sm font-medium disabled:opacity-50"
                            title="Write the translated subtitle next to the original media file"
                        >
                            <FolderOutput className="w-4 h-4" />
                            Export to Media
                        </button>
                    )}
                    <button
                        onClick={handleDownload}
                        className="flex items-center gap-2 px-4 py-2 bg-gray-900 text-white rounded-lg hover:bg-gray-805 transition-colors shadow-lg shadow-gray-900/20"
                        title="Download the subtitle file in your browser"
                    >
                        <Download className="w-4 h-4" />
                        Download
                    </button>
                </div>
            </header>

            {/* Filter / Search / Selection Toolbar */}
            <div className="bg-white border-b border-gray-200 px-6 py-2.5 flex flex-wrap items-center gap-3 z-10">
                <div className="relative flex-1 min-w-[220px] max-w-md">
                    <Search className="absolute left-3 top-2 h-4 w-4 text-gray-400" />
                    <input
                        type="text"
                        placeholder="Search original or translated text..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="w-full pl-9 pr-8 py-1.5 text-sm bg-gray-50 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:bg-white"
                    />
                    {searchQuery && (
                        <button onClick={() => setSearchQuery('')} className="absolute right-2 top-2 text-gray-400 hover:text-gray-600">
                            <X size={14} />
                        </button>
                    )}
                </div>
                <div className="flex gap-1.5">
                    {[
                        { id: 'all', label: 'All' },
                        { id: 'untranslated', label: 'Untranslated' },
                        { id: 'review', label: 'Needs Review' },
                        { id: 'edited', label: 'Edited' },
                    ].map(btn => (
                        <button
                            key={btn.id}
                            onClick={() => setFilterStatus(btn.id)}
                            className={`px-2.5 py-1 text-xs font-medium rounded-lg border transition-all ${
                                filterStatus === btn.id
                                    ? 'bg-indigo-600 border-indigo-600 text-white'
                                    : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'
                            }`}
                        >
                            {btn.label} ({counts[btn.id]})
                        </button>
                    ))}
                </div>
                {selected.size > 0 && (
                    <div className="flex items-center gap-2 ml-auto">
                        <span className="text-xs font-semibold text-indigo-600">{selected.size} selected</span>
                        {selected.size === 1 && (
                            <div className="flex items-center gap-1.5 pr-2 mr-1 border-r border-gray-200">
                                <span className="text-[11px] font-medium text-gray-400 select-none">Realign:</span>
                                <button
                                    onClick={() => handleShiftTranslations('up')}
                                    disabled={isLoading}
                                    className="flex items-center gap-1 px-2.5 py-1.5 text-xs font-semibold text-indigo-600 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200 rounded-lg transition-colors disabled:opacity-50"
                                    title="Shift every translation from this cue downward UP by one (fixes a split/shift where text sits one cue too low). Timecodes don't move; review then Save."
                                >
                                    <ArrowUp size={13} />
                                    Shift up
                                </button>
                                <button
                                    onClick={() => handleShiftTranslations('down')}
                                    disabled={isLoading}
                                    className="flex items-center gap-1 px-2.5 py-1.5 text-xs font-semibold text-indigo-600 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200 rounded-lg transition-colors disabled:opacity-50"
                                    title="Shift every translation from this cue downward DOWN by one (insert a gap here when a translation is missing). Timecodes don't move; review then Save."
                                >
                                    <ArrowDown size={13} />
                                    Shift down
                                </button>
                            </div>
                        )}
                        <button
                            onClick={handleDeleteSelected}
                            disabled={isLoading}
                            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-red-600 bg-red-50 hover:bg-red-100 border border-red-200 rounded-lg transition-colors disabled:opacity-50"
                            title="Delete selected lines permanently"
                        >
                            <Trash2 size={13} />
                            Delete Selected
                        </button>
                        <button
                            onClick={() => setSelected(new Set())}
                            className="px-3 py-1.5 text-xs font-medium text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
                        >
                            Clear
                        </button>
                    </div>
                )}
            </div>

            {/* Main Content - Side by Side Grid */}
            <div className="flex-1 overflow-y-auto p-6">
                <div className="max-w-7xl mx-auto bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
                    {/* Table Header */}
                    <div className="grid grid-cols-[44px_100px_1fr_1fr] bg-gray-50 border-b border-gray-200 font-semibold text-gray-500 text-sm sticky top-0 z-10">
                        <div className="p-3 border-r border-gray-200 flex items-center justify-center">
                            <input
                                ref={selectAllRef}
                                type="checkbox"
                                checked={allVisibleSelected}
                                onChange={handleToggleSelectVisible}
                                disabled={visibleRows.length === 0}
                                className="w-4 h-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                                title="Select all visible lines"
                            />
                        </div>
                        <div className="p-4 border-r border-gray-200">Timecode</div>
                        <div className="p-4 border-r border-gray-200">Original</div>
                        <div className="p-4">Translated</div>
                    </div>

                    {/* Table Body */}
                    <div className="divide-y divide-gray-100">
                        {visibleRows.length === 0 ? (
                            <div className="p-10 text-center text-sm text-gray-400">
                                No lines match the current search/filter.
                            </div>
                        ) : (
                            visibleRows.map(({ item, idx }) => (
                                <SubtitleRow
                                    key={item.id}
                                    item={item}
                                    idx={idx}
                                    isLoading={isLoading}
                                    projectName={projectName}
                                    episodeName={episodeName}
                                    selected={selected.has(item.id)}
                                    onChangeTranslation={handleTranslationChange}
                                    onResolved={handleResolved}
                                    onUpdateTm={handleUpdateTm}
                                    onToggleSelect={handleToggleSelect}
                                    onDeleteLine={handleDeleteLine}
                                />
                            ))
                        )}
                    </div>
                </div>
            </div>

            {/* Glossary Modal */}
            <AnimatePresence>
                {showGlossary && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4"
                    >
                        <motion.div
                            initial={{ scale: 0.95, opacity: 0 }}
                            animate={{ scale: 1, opacity: 1 }}
                            exit={{ scale: 0.95, opacity: 0 }}
                            className="bg-white rounded-2xl shadow-2xl w-full max-w-5xl h-[90vh] overflow-hidden flex flex-col"
                        >
                            <div className="p-4 border-b border-gray-100 flex justify-between items-center bg-gray-50">
                                <h2 className="text-lg font-bold text-gray-800 flex items-center gap-2">
                                    <Book className="w-5 h-5 text-indigo-600" />
                                    Glossary & Context
                                </h2>
                                <button
                                    onClick={() => setShowGlossary(false)}
                                    className="p-2 hover:bg-gray-200 rounded-full transition-colors"
                                >
                                    <X className="w-5 h-5 text-gray-500" />
                                </button>
                            </div>

                            <div className="flex-1 overflow-hidden bg-gray-50">
                                <GlossaryEditor
                                    glossary={glossary}
                                    onSave={(updatedGlossary, globalSave) => {
                                        if (onUpdateGlossary) {
                                            onUpdateGlossary(updatedGlossary, globalSave);
                                        }
                                        setShowGlossary(false);
                                    }}
                                    onCancel={() => setShowGlossary(false)}
                                    isSaving={isLoading}
                                    onUpdateTermsInScenes={onUpdateTermsInScenes}
                                />
                            </div>
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>
        </motion.div>
    );
};

export default EditorView;
