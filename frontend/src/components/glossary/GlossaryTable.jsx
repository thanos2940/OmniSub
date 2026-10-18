import React from 'react';
import {
    Book, Globe, AlertCircle, ChevronDown, ChevronUp, Check, RotateCcw, ArrowUpCircle, Trash2, EyeOff
} from 'lucide-react';

const GlossaryTable = ({
    filteredTerms,
    selectedTerms,
    toggleSelectTerm,
    toggleSelectAllFiltered,
    expandedDiffUid,
    setExpandedDiffUid,
    handleTermChange,
    handleAdoptUpstream,
    handleRevertToUniverse,
    handleSilenceUpstreamDiff,
    handleInlinePromote,
    handleRemoveTerm,
    promotingTerm,
    parentProject,
    readOnly = false,
    allTypes = ['Person', 'Location', 'Item', 'Organization', 'Technique', 'Concept', 'Other'],
}) => {
    return (
        <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
                <thead>
                    <tr className="border-b border-gray-100 dark:border-gray-700 bg-gray-50/80 dark:bg-gray-850/80 text-[11px] font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                        {!readOnly && (
                            <th className="p-3 w-10 text-center">
                                <input
                                    type="checkbox"
                                    checked={filteredTerms.length > 0 && filteredTerms.every(t => selectedTerms.has(t.term._uid))}
                                    onChange={toggleSelectAllFiltered}
                                    className="rounded text-indigo-600 focus:ring-indigo-500 w-3.5 h-3.5 border-gray-300"
                                />
                            </th>
                        )}
                        <th className="p-3 min-w-[180px]">Source Term</th>
                        <th className="p-3 min-w-[200px]">Translation</th>
                        <th className="p-3 w-32">Type</th>
                        <th className="p-3 w-28">Gender</th>
                        <th className="p-3 min-w-[200px]">Notes &amp; Used as</th>
                        <th className="p-3 w-28 text-center">Actions</th>
                    </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-750 text-xs">
                    {filteredTerms.length === 0 ? (
                        <tr>
                            <td colSpan={readOnly ? 6 : 7} className="text-center py-12 text-gray-400">
                                <Book className="w-8 h-8 mx-auto mb-2 opacity-40" />
                                <p className="font-semibold text-gray-500 dark:text-gray-400">No glossary terms match your filter</p>
                                <p className="text-[11px] mt-0.5">Try clearing your search or adding new terms.</p>
                            </td>
                        </tr>
                    ) : (
                        filteredTerms.map(({ term, index }) => {
                            const isSelected = selectedTerms.has(term._uid);
                            const isModifiedDiffOpen = expandedDiffUid === term._uid;
                            const parentDef = term.parent_term;

                            return (
                                <React.Fragment key={term._uid}>
                                    <tr className={`transition-colors ${
                                        isSelected
                                            ? 'bg-indigo-50/40 dark:bg-indigo-950/20'
                                            : term.upstream_modified
                                                ? 'bg-amber-50/30 dark:bg-amber-950/15 hover:bg-amber-50/50'
                                                : 'hover:bg-gray-50/60 dark:hover:bg-gray-750/40'
                                    }`}>
                                        {!readOnly && (
                                            <td className="p-3 text-center">
                                                <input
                                                    type="checkbox"
                                                    checked={isSelected}
                                                    onChange={() => toggleSelectTerm(term._uid)}
                                                    className="rounded text-indigo-600 focus:ring-indigo-500 w-3.5 h-3.5 border-gray-300"
                                                />
                                            </td>
                                        )}

                                        {/* Term Name & Origin Badges */}
                                        <td className="p-3">
                                            <div className="flex flex-col gap-1">
                                                <input
                                                    type="text"
                                                    value={term.term || ''}
                                                    disabled={readOnly}
                                                    onChange={(e) => handleTermChange(index, 'term', e.target.value)}
                                                    className="w-full font-bold text-gray-900 dark:text-white bg-transparent border-0 focus:ring-1 focus:ring-indigo-500 rounded px-1.5 py-0.5"
                                                    placeholder="English Term"
                                                />
                                                <div className="flex items-center gap-1.5 flex-wrap pl-1.5">
                                                    {term.inherited ? (
                                                        <span className="text-[10px] font-semibold px-1.5 py-0.2 rounded bg-purple-50 dark:bg-purple-950 text-purple-700 dark:text-purple-300 border border-purple-200 dark:border-purple-900/60 flex items-center gap-1">
                                                            <Globe className="w-2.5 h-2.5" />
                                                            {term.inherited_from || 'Universe'}
                                                        </span>
                                                    ) : term.is_override ? (
                                                        <span className="text-[10px] font-semibold px-1.5 py-0.2 rounded bg-amber-50 dark:bg-amber-950 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-900/60">
                                                            Local Override
                                                        </span>
                                                    ) : (
                                                        <span className="text-[10px] font-semibold px-1.5 py-0.2 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
                                                            Project Only
                                                        </span>
                                                    )}

                                                    {term.upstream_modified && (
                                                        <button
                                                            type="button"
                                                            onClick={() => setExpandedDiffUid(isModifiedDiffOpen ? null : term._uid)}
                                                            className="text-[10px] font-bold px-1.5 py-0.2 rounded bg-amber-500 text-white flex items-center gap-1 animate-pulse shadow-sm"
                                                            title="Parent definition changed upstream. Click to compare."
                                                        >
                                                            <AlertCircle className="w-2.5 h-2.5" />
                                                            Upstream Changed
                                                            {isModifiedDiffOpen ? <ChevronUp className="w-2.5 h-2.5" /> : <ChevronDown className="w-2.5 h-2.5" />}
                                                        </button>
                                                    )}
                                                </div>
                                            </div>
                                        </td>

                                        {/* Translation */}
                                        <td className="p-3">
                                            <input
                                                type="text"
                                                value={term.translation || ''}
                                                disabled={readOnly}
                                                onChange={(e) => handleTermChange(index, 'translation', e.target.value)}
                                                className="w-full font-semibold text-indigo-600 dark:text-indigo-400 bg-transparent border-0 focus:ring-1 focus:ring-indigo-500 rounded px-1.5 py-0.5"
                                                placeholder="Target Translation"
                                            />
                                        </td>

                                        {/* Type Dropdown */}
                                        <td className="p-3">
                                            <select
                                                value={term.type || 'other'}
                                                disabled={readOnly}
                                                onChange={(e) => handleTermChange(index, 'type', e.target.value)}
                                                className="w-full text-xs font-semibold rounded-lg border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-750 text-gray-800 dark:text-gray-200 py-1 px-2 focus:ring-1 focus:ring-indigo-500"
                                            >
                                                {allTypes.map(t => (
                                                    <option key={t} value={t.toLowerCase()}>{t}</option>
                                                ))}
                                            </select>
                                        </td>

                                        {/* Gender Dropdown */}
                                        <td className="p-3">
                                            <select
                                                value={term.gender || 'neuter'}
                                                disabled={readOnly}
                                                onChange={(e) => handleTermChange(index, 'gender', e.target.value)}
                                                className="w-full text-xs rounded-lg border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-750 text-gray-800 dark:text-gray-200 py-1 px-2 focus:ring-1 focus:ring-indigo-500"
                                            >
                                                <option value="masculine">Masc (αρσ)</option>
                                                <option value="feminine">Fem (θηλ)</option>
                                                <option value="neuter">Neut (ουδ)</option>
                                                <option value="n/a">N/A</option>
                                            </select>
                                        </td>

                                        {/* Description & Flags */}
                                        <td className="p-3">
                                            <div className="flex flex-col gap-1">
                                                <input
                                                    type="text"
                                                    value={term.description || ''}
                                                    disabled={readOnly}
                                                    onChange={(e) => handleTermChange(index, 'description', e.target.value)}
                                                    className="w-full text-xs text-gray-600 dark:text-gray-300 bg-transparent border-0 focus:ring-1 focus:ring-indigo-500 rounded px-1.5 py-0.5"
                                                    placeholder="Context notes..."
                                                />
                                                <input
                                                    type="text"
                                                    value={term.sense || ''}
                                                    disabled={readOnly}
                                                    onChange={(e) => handleTermChange(index, 'sense', e.target.value)}
                                                    className="w-full text-[11px] italic text-gray-500 dark:text-gray-400 bg-transparent border-0 focus:ring-1 focus:ring-indigo-500 rounded px-1.5 py-0.5"
                                                    placeholder="Used as… (only for words with an everyday meaning)"
                                                    title={'The sense in which this rendering applies, e.g. "the menu command" for Load. Elsewhere the word is translated by its meaning ("a heavy load").'}
                                                />
                                                <div className="flex items-center gap-3 pl-1.5 text-[11px] text-gray-400">
                                                    <label className="flex items-center gap-1 cursor-pointer select-none">
                                                        <input
                                                            type="checkbox"
                                                            checked={term.case_sensitive ?? false}
                                                            disabled={readOnly}
                                                            onChange={(e) => handleTermChange(index, 'case_sensitive', e.target.checked)}
                                                            className="rounded text-indigo-600 w-3 h-3"
                                                        />
                                                        <span>Case Sensitive</span>
                                                    </label>
                                                    <label className="flex items-center gap-1 cursor-pointer select-none">
                                                        <input
                                                            type="checkbox"
                                                            checked={term.keep_original ?? false}
                                                            disabled={readOnly}
                                                            onChange={(e) => handleTermChange(index, 'keep_original', e.target.checked)}
                                                            className="rounded text-indigo-600 w-3 h-3"
                                                        />
                                                        <span>Keep Original</span>
                                                    </label>
                                                </div>
                                            </div>
                                        </td>

                                        {/* Actions */}
                                        <td className="p-3 text-center">
                                            <div className="flex items-center justify-center gap-1">
                                                {/* Promote to Universe */}
                                                {!term.inherited && parentProject && !readOnly && (
                                                    <button
                                                        type="button"
                                                        onClick={() => handleInlinePromote(term)}
                                                        disabled={promotingTerm === term.term}
                                                        className="p-1.5 text-purple-600 hover:bg-purple-50 dark:hover:bg-purple-950/50 rounded-lg transition-colors"
                                                        title={`Promote to ${parentProject} universe`}
                                                    >
                                                        <ArrowUpCircle className={`w-4 h-4 ${promotingTerm === term.term ? 'animate-spin' : ''}`} />
                                                    </button>
                                                )}

                                                {/* Revert override */}
                                                {term.is_override && !readOnly && (
                                                    <button
                                                        type="button"
                                                        onClick={() => handleRevertToUniverse(index)}
                                                        className="p-1.5 text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-950/50 rounded-lg transition-colors"
                                                        title="Revert override to parent universe definition"
                                                    >
                                                        <RotateCcw className="w-4 h-4" />
                                                    </button>
                                                )}

                                                {/* Delete / Suppress */}
                                                {!readOnly && (
                                                    <button
                                                        type="button"
                                                        onClick={() => handleRemoveTerm(index)}
                                                        className="p-1.5 text-gray-400 hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950/50 rounded-lg transition-colors"
                                                        title={term.inherited ? "Suppress from this show" : "Delete term"}
                                                    >
                                                        {term.inherited ? <EyeOff className="w-4 h-4" /> : <Trash2 className="w-4 h-4" />}
                                                    </button>
                                                )}
                                            </div>
                                        </td>
                                    </tr>

                                    {/* Upstream Diff Accordion Row */}
                                    {isModifiedDiffOpen && parentDef && (
                                        <tr className="bg-amber-50/60 dark:bg-amber-950/30 border-b border-amber-200 dark:border-amber-900/60">
                                            <td colSpan={readOnly ? 6 : 7} className="p-4">
                                                <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
                                                    <div className="space-y-1">
                                                        <div className="text-xs font-bold text-amber-900 dark:text-amber-200 flex items-center gap-1.5">
                                                            <AlertCircle className="w-4 h-4 text-amber-600" />
                                                            Upstream Parent Definition:
                                                        </div>
                                                        <div className="text-xs text-amber-800 dark:text-amber-300 font-mono bg-white/70 dark:bg-gray-850 p-2 rounded-lg border border-amber-200 dark:border-amber-900">
                                                            <strong>{parentDef.term}</strong> &rarr; <span className="text-indigo-600 dark:text-indigo-400 font-bold">{parentDef.translation}</span> | Type: {parentDef.type} | Gender: {parentDef.gender} | Notes: {parentDef.description || 'none'}
                                                        </div>
                                                    </div>

                                                    <div className="flex items-center gap-2">
                                                        <button
                                                            type="button"
                                                            onClick={() => handleAdoptUpstream(index)}
                                                            className="px-3 py-1.5 bg-amber-600 hover:bg-amber-700 text-white rounded-lg text-xs font-bold flex items-center gap-1 shadow-sm transition-all"
                                                        >
                                                            <Check className="w-3.5 h-3.5" /> Adopt Upstream
                                                        </button>
                                                        <button
                                                            type="button"
                                                            onClick={() => handleRevertToUniverse(index)}
                                                            className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-bold flex items-center gap-1 shadow-sm transition-all"
                                                        >
                                                            <RotateCcw className="w-3.5 h-3.5" /> Revert to Parent
                                                        </button>
                                                        <button
                                                            type="button"
                                                            onClick={() => handleSilenceUpstreamDiff(index)}
                                                            className="px-3 py-1.5 bg-white dark:bg-gray-800 hover:bg-gray-100 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-gray-700 rounded-lg text-xs font-semibold transition-colors"
                                                        >
                                                            Keep Local
                                                        </button>
                                                    </div>
                                                </div>
                                            </td>
                                        </tr>
                                    )}
                                </React.Fragment>
                            );
                        })
                    )}
                </tbody>
            </table>
        </div>
    );
};

export default GlossaryTable;
