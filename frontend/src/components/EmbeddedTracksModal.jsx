import React, { useState, useEffect } from 'react';
import { api } from '../api';
import { useToast } from '../context/ToastContext';
import {
    Film, X, RefreshCw, Check, AlertCircle, AlertTriangle,
    Layers, Sparkles, Download, CheckCircle2, FileText, ChevronRight, Terminal
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

const EmbeddedTracksModal = ({
    isOpen,
    onClose,
    projectName,
    episodeName,
    onExtractionComplete,
}) => {
    const toast = useToast();
    const [loading, setLoading] = useState(true);
    const [probeData, setProbeData] = useState(null);
    const [selectedStreamIndex, setSelectedStreamIndex] = useState(null);
    const [migrateSrt, setMigrateSrt] = useState(true);
    const [forceOverwrite, setForceOverwrite] = useState(false);
    const [autoTranslate, setAutoTranslate] = useState(false);
    const [extracting, setExtracting] = useState(false);
    const [extractLogs, setExtractLogs] = useState([]);
    const [extractResult, setExtractResult] = useState(null);

    const loadProbeData = async (forceRefresh = false) => {
        if (!projectName || !episodeName) return;
        setLoading(true);
        setExtractResult(null);
        try {
            const res = await api.probeEpisodeEmbedded(projectName, episodeName, forceRefresh);
            setProbeData(res.data);
            if (res.data?.recommended_stream_index !== null && res.data?.recommended_stream_index !== undefined) {
                setSelectedStreamIndex(res.data.recommended_stream_index);
            } else if (res.data?.tracks?.length > 0) {
                setSelectedStreamIndex(res.data.tracks[0].index);
            }
        } catch (err) {
            console.error('Failed to probe media container', err);
            toast.error(err.response?.data?.detail || 'Failed to probe media container');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        if (isOpen) {
            setExtractLogs([]);
            setExtractResult(null);
            loadProbeData(false);
        }
    }, [isOpen, projectName, episodeName]);

    if (!isOpen) return null;

    const handleExtract = async (streamIndexToUse = null) => {
        const streamIdx = streamIndexToUse !== null ? streamIndexToUse : selectedStreamIndex;
        setExtracting(true);
        setExtractLogs([
            `[${new Date().toLocaleTimeString()}] Initiating extraction of stream #${streamIdx ?? 'auto'} for ${episodeName}...`,
            `[${new Date().toLocaleTimeString()}] Media path: ${probeData?.media_path || 'unknown'}`,
        ]);
        try {
            const res = await api.extractEpisodeEmbedded(projectName, episodeName, {
                stream_index: streamIdx,
                force: forceOverwrite,
                migrate_srt: migrateSrt,
                auto_translate: autoTranslate,
            });

            const data = res.data;
            setExtractResult(data);
            setExtractLogs(prev => [
                ...prev,
                `[${new Date().toLocaleTimeString()}] ✓ Successfully extracted stream #${data.stream_index} ("${data.track_title}").`,
                `[${new Date().toLocaleTimeString()}] ✓ Wrote sidecar: ${data.sidecar_path}`,
                `[${new Date().toLocaleTimeString()}] ✓ Imported ${data.line_count} dialogue cues into episode.`,
                ...(data.migrated_sibling ? [`[${new Date().toLocaleTimeString()}] ℹ Moved previous SRT translation to '${data.migrated_sibling}'.`] : []),
                `[${new Date().toLocaleTimeString()}] Done!`
            ]);

            toast.success(`Extracted ${data.line_count} cues from stream #${data.stream_index}!`);
            if (onExtractionComplete) {
                onExtractionComplete(data);
            }
        } catch (err) {
            console.error('Extraction failed', err);
            const msg = err.response?.data?.detail || err.message || 'Extraction failed';
            setExtractLogs(prev => [
                ...prev,
                `[${new Date().toLocaleTimeString()}] ✗ Extraction failed: ${msg}`
            ]);
            toast.error(msg);
        } finally {
            setExtracting(false);
        }
    };

    const hasMedia = probeData?.has_media;
    const toolsAvailable = probeData?.tools_available;
    const tracks = probeData?.tracks || [];
    const recommendedIndex = probeData?.recommended_stream_index;

    return (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-black/60 backdrop-blur-sm flex items-center justify-center p-4">
            <motion.div
                initial={{ scale: 0.95, opacity: 0, y: 10 }}
                animate={{ scale: 1, opacity: 1, y: 0 }}
                exit={{ scale: 0.95, opacity: 0, y: 10 }}
                className="bg-white dark:bg-gray-850 rounded-2xl max-w-3xl w-full shadow-2xl border border-gray-200 dark:border-gray-700 overflow-hidden flex flex-col max-h-[90vh]"
            >
                {/* Modal Header */}
                <div className="px-6 py-4 border-b border-gray-100 dark:border-gray-700/80 flex items-center justify-between bg-gradient-to-r from-purple-50/50 to-indigo-50/50 dark:from-purple-950/20 dark:to-indigo-950/20">
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-xl bg-purple-100 text-purple-600 dark:bg-purple-900/40 dark:text-purple-400 flex items-center justify-center shadow-sm">
                            <Film size={20} />
                        </div>
                        <div>
                            <div className="flex items-center gap-2">
                                <h3 className="font-bold text-gray-900 dark:text-white text-lg">
                                    Embedded Subtitle Tracks
                                </h3>
                                <span className="px-2 py-0.5 rounded-full text-xs font-semibold bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300">
                                    {episodeName}
                                </span>
                            </div>
                            <p className="text-xs text-gray-500 dark:text-gray-400 truncate max-w-md" title={probeData?.media_path || ''}>
                                {probeData?.media_filename || 'Loading media container...'}
                            </p>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <button
                            onClick={() => loadProbeData(true)}
                            disabled={loading || extracting}
                            className="p-2 text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-xl transition-all"
                            title="Re-probe media file"
                        >
                            <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
                        </button>
                        <button
                            onClick={onClose}
                            disabled={extracting}
                            className="p-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-xl transition-all"
                        >
                            <X size={20} />
                        </button>
                    </div>
                </div>

                {/* Modal Body */}
                <div className="p-6 overflow-y-auto space-y-5 flex-1">
                    {loading ? (
                        <div className="py-16 text-center space-y-3">
                            <RefreshCw className="w-8 h-8 mx-auto text-purple-600 animate-spin" />
                            <p className="text-sm font-medium text-gray-600 dark:text-gray-300">
                                Probing video container with ffprobe...
                            </p>
                            <p className="text-xs text-gray-400">Reading container headers to detect all muxed subtitle streams</p>
                        </div>
                    ) : !hasMedia ? (
                        <div className="p-6 rounded-xl bg-rose-50 dark:bg-rose-950/30 border border-rose-200 dark:border-rose-800/50 text-center space-y-2">
                            <AlertCircle className="w-8 h-8 text-rose-500 mx-auto" />
                            <h4 className="font-semibold text-rose-900 dark:text-rose-200">No Reachable Media File</h4>
                            <p className="text-xs text-rose-700 dark:text-rose-300 max-w-md mx-auto">
                                {probeData?.error || 'This episode is not linked to a video file, or the media path is unreachable on disk.'}
                            </p>
                            <p className="text-[11px] text-gray-400 mt-2">
                                Sync with Sonarr/Radarr or ensure the media file sits next to the episode.
                            </p>
                        </div>
                    ) : !toolsAvailable ? (
                        <div className="p-6 rounded-xl bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800/50 text-center space-y-2">
                            <AlertTriangle className="w-8 h-8 text-amber-500 mx-auto" />
                            <h4 className="font-semibold text-amber-900 dark:text-amber-200">FFmpeg / FFprobe Unavailable</h4>
                            <p className="text-xs text-amber-700 dark:text-amber-300 max-w-md mx-auto">
                                Omnisub could not locate ffmpeg / ffprobe. Please install ffmpeg on your system PATH or configure its path in Settings.
                            </p>
                        </div>
                    ) : tracks.length === 0 ? (
                        <div className="py-12 text-center text-gray-400 space-y-2">
                            <Layers className="w-8 h-8 mx-auto opacity-40" />
                            <p className="text-sm font-medium text-gray-600 dark:text-gray-300">No Subtitle Streams Found</p>
                            <p className="text-xs text-gray-400">The video container does not contain any embedded subtitle streams.</p>
                        </div>
                    ) : (
                        <>
                            {/* Summary Stat Pills */}
                            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                                <div className="p-3 bg-gray-50 dark:bg-gray-800/70 rounded-xl border border-gray-150 dark:border-gray-700 text-center">
                                    <div className="text-xs text-gray-400 font-medium">Total Streams</div>
                                    <div className="text-lg font-bold text-gray-800 dark:text-white">{probeData.total_tracks}</div>
                                </div>
                                <div className="p-3 bg-purple-50/60 dark:bg-purple-950/20 rounded-xl border border-purple-100 dark:border-purple-900/40 text-center">
                                    <div className="text-xs text-purple-600 dark:text-purple-400 font-medium">Text Subtitles (ASS/SRT)</div>
                                    <div className="text-lg font-bold text-purple-700 dark:text-purple-300">
                                        {(probeData.ass_tracks_count || 0) + (probeData.srt_tracks_count || 0)}
                                    </div>
                                </div>
                                <div className="p-3 bg-gray-50 dark:bg-gray-800/70 rounded-xl border border-gray-150 dark:border-gray-700 text-center">
                                    <div className="text-xs text-gray-400 font-medium">Current Format</div>
                                    <div className="text-lg font-bold text-gray-800 dark:text-white uppercase">{probeData.current_format || 'SRT'}</div>
                                </div>
                                <div className="p-3 bg-emerald-50/60 dark:bg-emerald-950/20 rounded-xl border border-emerald-100 dark:border-emerald-900/40 text-center">
                                    <div className="text-xs text-emerald-600 dark:text-emerald-400 font-medium">Sidecar on Disk</div>
                                    <div className="text-lg font-bold text-emerald-700 dark:text-emerald-300">
                                        {probeData.sidecar_exists ? `Present (.${probeData.current_format || 'ass'})` : 'None'}
                                    </div>
                                </div>
                            </div>

                            {/* Streams Table */}
                            <div>
                                <div className="flex items-center justify-between mb-2">
                                    <h4 className="text-xs font-bold text-gray-500 uppercase tracking-wider">
                                        Detected Subtitle Streams ({tracks.length})
                                    </h4>
                                    <span className="text-xs text-gray-400">
                                        Source Language Code: <span className="font-mono font-semibold text-gray-600 dark:text-gray-300">{probeData.source_lang_code}</span>
                                    </span>
                                </div>

                                <div className="space-y-2">
                                    {tracks.map((track) => {
                                        const isSelected = selectedStreamIndex === track.index;
                                        const isRecommended = track.is_recommended;
                                        const isAss = track.is_ass;
                                        const isSrt = track.is_srt;
                                        const isImage = track.is_image;

                                        return (
                                            <div
                                                key={track.index}
                                                onClick={() => setSelectedStreamIndex(track.index)}
                                                className={`p-3.5 rounded-xl border transition-all cursor-pointer flex items-center justify-between gap-4 ${
                                                    isSelected
                                                        ? 'border-indigo-500 bg-indigo-50/40 dark:bg-indigo-950/30 ring-1 ring-indigo-500'
                                                        : 'border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600 bg-white dark:bg-gray-800/80'
                                                }`}
                                            >
                                                <div className="flex items-center gap-3.5 min-w-0">
                                                    <input
                                                        type="radio"
                                                        name="selected_stream"
                                                        checked={isSelected}
                                                        onChange={() => setSelectedStreamIndex(track.index)}
                                                        className="w-4 h-4 text-indigo-600 border-gray-300 focus:ring-indigo-500"
                                                    />

                                                    <div>
                                                        <div className="flex items-center gap-2 flex-wrap">
                                                            <span className="font-mono text-xs font-bold text-gray-500 dark:text-gray-400 bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded">
                                                                #{track.index}
                                                            </span>

                                                            {/* Codec Badge */}
                                                            <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-tight ${
                                                                isAss
                                                                    ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300'
                                                                    : isSrt
                                                                        ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
                                                                        : isImage
                                                                            ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
                                                                            : 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300'
                                                            }`}>
                                                                {track.codec || 'unknown'}
                                                            </span>

                                                            {/* Language Badge */}
                                                            <span className="text-xs font-semibold text-gray-700 dark:text-gray-200 bg-gray-100 dark:bg-gray-700/60 px-2 py-0.5 rounded uppercase">
                                                                {track.language || 'und'}
                                                            </span>

                                                            {/* Title */}
                                                            <span className="text-sm font-medium text-gray-800 dark:text-gray-200 truncate max-w-[220px]">
                                                                {track.title || 'Untitled Stream'}
                                                            </span>

                                                            {/* Flags */}
                                                            {track.default && (
                                                                <span className="text-[10px] font-bold bg-blue-50 text-blue-600 dark:bg-blue-950 dark:text-blue-400 px-1.5 py-0.5 rounded">
                                                                    DEFAULT
                                                                </span>
                                                            )}
                                                            {track.forced && (
                                                                <span className="text-[10px] font-bold bg-rose-50 text-rose-600 dark:bg-rose-950 dark:text-rose-400 px-1.5 py-0.5 rounded">
                                                                    FORCED
                                                                </span>
                                                            )}
                                                        </div>

                                                        <div className="flex items-center gap-3 mt-1 text-xs text-gray-400">
                                                            {track.frames > 0 ? (
                                                                <span>{track.frames} dialogue cues / events</span>
                                                            ) : (
                                                                <span>Frames tag absent</span>
                                                            )}
                                                            {track.penalty_reasons?.length > 0 && (
                                                                <span className="text-amber-500 font-medium text-[11px]">
                                                                    Deprioritized: {track.penalty_reasons.join(', ')}
                                                                </span>
                                                            )}
                                                        </div>
                                                    </div>
                                                </div>

                                                {/* Recommendation Pill */}
                                                <div className="flex-shrink-0">
                                                    {isRecommended ? (
                                                        <span className="flex items-center gap-1 text-xs font-bold text-emerald-700 dark:text-emerald-300 bg-emerald-100 dark:bg-emerald-950/60 border border-emerald-300 dark:border-emerald-800 px-2.5 py-1 rounded-full shadow-sm">
                                                            <Sparkles size={12} /> Recommended
                                                        </span>
                                                    ) : track.penalized ? (
                                                        <span className="text-xs font-medium text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/40 px-2 py-0.5 rounded-full">
                                                            Signs / Partial
                                                        </span>
                                                    ) : isImage ? (
                                                        <span className="text-xs font-medium text-gray-400 bg-gray-100 dark:bg-gray-700 px-2 py-0.5 rounded-full">
                                                            Image (OCR needed)
                                                        </span>
                                                    ) : null}
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>

                            {/* Extraction Options */}
                            <div className="p-4 bg-gray-50 dark:bg-gray-800/60 rounded-xl border border-gray-200 dark:border-gray-700 space-y-2.5">
                                <h4 className="text-xs font-bold text-gray-500 uppercase tracking-wider">Extraction Options</h4>
                                <label className="flex items-center gap-2.5 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={migrateSrt}
                                        onChange={(e) => setMigrateSrt(e.target.checked)}
                                        className="w-4 h-4 rounded text-indigo-600 border-gray-300 focus:ring-indigo-500"
                                    />
                                    <span className="text-xs text-gray-700 dark:text-gray-300 font-medium">
                                        Preserve existing SRT translation as dual-format sibling <span className="font-mono text-indigo-600 dark:text-indigo-400">[{episodeName} [srt]]</span>
                                    </span>
                                </label>
                                <label className="flex items-center gap-2.5 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={forceOverwrite}
                                        onChange={(e) => setForceOverwrite(e.target.checked)}
                                        className="w-4 h-4 rounded text-indigo-600 border-gray-300 focus:ring-indigo-500"
                                    />
                                    <span className="text-xs text-gray-700 dark:text-gray-300 font-medium">
                                        Force overwrite existing <span className="font-mono">.ass</span> sidecar if already present on disk
                                    </span>
                                </label>
                            </div>

                            {/* Live Terminal Logs when extracting or completed */}
                            {extractLogs.length > 0 && (
                                <div className="rounded-xl border border-gray-850 bg-gray-900 text-gray-200 p-4 font-mono text-xs space-y-1 overflow-x-auto max-h-40">
                                    <div className="flex items-center gap-2 text-gray-400 border-b border-gray-800 pb-1 mb-1">
                                        <Terminal size={13} />
                                        <span>Extraction Execution Log</span>
                                    </div>
                                    {extractLogs.map((log, i) => (
                                        <div key={i} className="leading-relaxed">{log}</div>
                                    ))}
                                </div>
                            )}
                        </>
                    )}
                </div>

                {/* Modal Footer */}
                <div className="px-6 py-4 border-t border-gray-100 dark:border-gray-700/80 bg-gray-50/50 dark:bg-gray-850 flex items-center justify-between">
                    <button
                        onClick={onClose}
                        disabled={extracting}
                        className="px-4 py-2 text-xs font-semibold text-gray-600 dark:text-gray-400 hover:text-gray-800 dark:hover:text-white rounded-xl hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                    >
                        Close
                    </button>

                    <div className="flex items-center gap-2.5">
                        {recommendedIndex !== null && recommendedIndex !== undefined && (
                            <button
                                onClick={() => handleExtract(recommendedIndex)}
                                disabled={loading || extracting}
                                className="flex items-center gap-2 px-4 py-2.5 text-xs font-bold text-white bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-700 hover:to-indigo-700 rounded-xl shadow-md shadow-purple-500/20 disabled:opacity-50 transition-all"
                            >
                                <Sparkles size={14} className={extracting ? 'animate-spin' : ''} />
                                {extracting ? 'Extracting...' : `Auto-Extract Recommended (#${recommendedIndex})`}
                            </button>
                        )}

                        <button
                            onClick={() => handleExtract(selectedStreamIndex)}
                            disabled={loading || extracting || selectedStreamIndex === null}
                            className="flex items-center gap-1.5 px-4 py-2.5 text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-700 rounded-xl shadow-sm disabled:opacity-50 transition-all"
                        >
                            <Download size={14} />
                            {extracting ? 'Extracting...' : `Extract Stream #${selectedStreamIndex ?? '...'}`}
                        </button>
                    </div>
                </div>
            </motion.div>
        </div>
    );
};

export default EmbeddedTracksModal;
