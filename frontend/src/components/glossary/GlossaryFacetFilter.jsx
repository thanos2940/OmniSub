import { Search, Plus, EyeOff } from 'lucide-react';
import { SOURCE_FILTERS } from './useGlossaryState';

const GlossaryFacetFilter = ({
    searchFilter,
    setSearchFilter,
    sourceFilter,
    setSourceFilter,
    typeFilter,
    setTypeFilter,
    allTypes,
    totalTermsCount,
    inheritedCount,
    overrideCount,
    upstreamModifiedCount,
    suppressedCount,
    showSuppressed,
    setShowSuppressed,
    onAddTerm,
    readOnly = false,
}) => {
    return (
        <div className="space-y-2.5">
            {/* Search & Source Filter Tabs */}
            <div className="p-4 border-b border-gray-100 dark:border-gray-700 flex flex-wrap items-center justify-between gap-3 bg-gray-50/50 dark:bg-gray-850">
                <div className="flex items-center gap-3 flex-wrap flex-1 min-w-[280px]">
                    <div className="relative flex-1 max-w-sm">
                        <Search className="w-4 h-4 text-gray-400 absolute left-3 top-2.5" />
                        <input
                            type="text"
                            placeholder="Search terms, translations, notes..."
                            value={searchFilter}
                            onChange={(e) => setSearchFilter(e.target.value)}
                            className="w-full pl-9 pr-3 py-1.5 text-xs rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 focus:ring-1 focus:ring-indigo-500 text-gray-800 dark:text-gray-200 outline-none"
                        />
                    </div>

                    {/* Source Facet Pills */}
                    <div className="flex bg-gray-100 dark:bg-gray-700 p-1 rounded-xl gap-0.5 text-xs font-semibold">
                        {[
                            { id: SOURCE_FILTERS.ALL, label: 'All', count: totalTermsCount },
                            { id: SOURCE_FILTERS.INHERITED, label: 'Inherited', count: inheritedCount },
                            { id: SOURCE_FILTERS.OVERRIDE, label: 'Overrides', count: overrideCount },
                            { id: SOURCE_FILTERS.UPSTREAM_MODIFIED, label: 'Upstream Changed', count: upstreamModifiedCount, highlight: true },
                        ].map(({ id, label, count, highlight }) => {
                            if (highlight && count === 0) return null;
                            const isActive = sourceFilter === id;
                            return (
                                <button
                                    key={id}
                                    type="button"
                                    onClick={() => setSourceFilter(id)}
                                    className={`px-2.5 py-1 rounded-lg transition-all flex items-center gap-1.5 ${
                                        isActive
                                            ? highlight
                                                ? 'bg-amber-500 text-white shadow-sm'
                                                : 'bg-white dark:bg-gray-800 text-indigo-600 dark:text-indigo-400 shadow-sm'
                                            : highlight
                                                ? 'text-amber-600 dark:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-950/40'
                                                : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
                                    }`}
                                >
                                    <span>{label}</span>
                                    <span className={`text-[10px] px-1 py-0.2 rounded-full ${isActive ? 'bg-black/15 text-current' : 'bg-gray-200 dark:bg-gray-600 text-gray-600 dark:text-gray-300'}`}>
                                        {count}
                                    </span>
                                </button>
                            );
                        })}
                    </div>
                </div>
            </div>

            {/* Type Categories & Term Actions Sub-Bar */}
            <div className="px-4 py-2.5 border-b border-gray-100 dark:border-gray-700 flex flex-wrap items-center justify-between gap-3 bg-white dark:bg-gray-800">
                <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-[11px] font-bold text-gray-400 uppercase tracking-wider mr-1">Type:</span>
                    {['All', ...allTypes].map(type => (
                        <button
                            key={type}
                            type="button"
                            onClick={() => setTypeFilter(type)}
                            className={`px-2.5 py-1 rounded-lg text-xs font-semibold transition-all ${
                                typeFilter === type
                                    ? 'bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800'
                                    : 'text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                            }`}
                        >
                            {type}
                        </button>
                    ))}
                </div>

                {!readOnly && (
                    <div className="flex items-center gap-2">
                        <button
                            type="button"
                            onClick={onAddTerm}
                            className="px-3 py-1.5 bg-indigo-50 dark:bg-indigo-950/50 hover:bg-indigo-100 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800 rounded-xl text-xs font-bold flex items-center gap-1.5 transition-colors"
                        >
                            <Plus className="w-3.5 h-3.5" /> Add Term
                        </button>
                        {suppressedCount > 0 && (
                            <button
                                type="button"
                                onClick={() => setShowSuppressed(!showSuppressed)}
                                className={`px-2.5 py-1.5 rounded-xl text-xs font-semibold border flex items-center gap-1.5 transition-all ${
                                    showSuppressed
                                        ? 'bg-amber-100 border-amber-300 text-amber-800 dark:bg-amber-950/40 dark:border-amber-800 dark:text-amber-300'
                                        : 'bg-gray-50 dark:bg-gray-750 border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300'
                                }`}
                            >
                                <EyeOff className="w-3.5 h-3.5 text-amber-500" />
                                <span>Suppressed ({suppressedCount})</span>
                            </button>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};

export default GlossaryFacetFilter;
