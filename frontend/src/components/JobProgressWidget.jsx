import { useState, useRef, useEffect, useMemo } from 'react';
import { useJobs } from '../context/JobContext';
import { apiClient } from '../api';
import { X, ChevronDown, Activity, CheckCircle, AlertCircle, Terminal, ExternalLink, Sparkles, Maximize2, Copy, Check, Download, ArrowDown, Trash2 } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { useLocation, useNavigate } from 'react-router-dom';

const renderColorizedLog = (log) => {
    if (!log) return null;

    // Parse leading timestamp: [HH:MM:SS]
    const timestampMatch = log.match(/^(\[\d{2}:\d{2}:\d{2}\])\s*(.*)$/);
    let timestamp = null;
    let text = log;
    if (timestampMatch) {
        timestamp = timestampMatch[1];
        text = timestampMatch[2];
    }

    let colorClass = "text-gray-300";
    const lower = text.toLowerCase();

    if (lower.includes("failed") || lower.includes("failure") || lower.includes("error") || lower.includes("exception") || lower.includes("✗")) {
        colorClass = "text-red-400 font-semibold";
    } else if (lower.includes("warning") || lower.includes("incomplete") || lower.includes("limit") || lower.includes("retry") || lower.includes("retrying")) {
        colorClass = "text-amber-400 font-medium";
    } else if (lower.includes("export") || lower.includes("saved") || lower.includes("save") || lower.includes("stored") || lower.includes("checkpoint") || lower.includes("✓")) {
        colorClass = "text-emerald-400";
    } else if (lower.includes("translating") || lower.includes("translation") || lower.includes("retranslate") || lower.includes("reasoning:") || lower.includes("reused")) {
        colorClass = "text-purple-400";
    } else if (lower.includes("running") || lower.includes("starting") || lower.includes("initializing") || lower.includes("syncing") || lower.includes("extracting") || lower.includes("reviewing")) {
        colorClass = "text-blue-400";
    }

    return (
        <span className="font-mono">
            {timestamp && <span className="text-gray-500 select-none mr-2 font-sans text-[11px]">{timestamp}</span>}
            <span className={colorClass}>{text}</span>
        </span>
    );
};

const getSceneColor = (status) => {
    switch (status) {
        case 'completed':
            return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40 dark:bg-emerald-950/60 dark:text-emerald-300';
        case 'running':
            return 'bg-blue-500/20 text-blue-400 border-blue-400 dark:bg-blue-950/60 dark:text-blue-300 animate-pulse font-medium';
        case 'pending':
            return 'bg-amber-500/20 text-amber-400 border-amber-500/40 dark:bg-amber-950/50 dark:text-amber-300';
        case 'failed':
        case 'retry':
        case 'retrying':
            return 'bg-rose-500/20 text-rose-400 border-rose-500/50 dark:bg-rose-950/60 dark:text-rose-300 animate-pulse font-medium';
        default:
            return 'bg-gray-200 text-gray-600 dark:bg-gray-700 dark:text-gray-300 border border-gray-600';
    }
};

const getSortedScenes = (sceneStatus) => {
    if (!sceneStatus || typeof sceneStatus !== 'object') return [];
    return Object.entries(sceneStatus).sort(([keyA], [keyB]) => {
        const numA = parseInt(keyA.replace(/\D+/g, ''), 10);
        const numB = parseInt(keyB.replace(/\D+/g, ''), 10);
        if (!isNaN(numA) && !isNaN(numB)) {
            return numA - numB;
        }
        return keyA.localeCompare(keyB, undefined, { numeric: true });
    });
};

const renderStatusPill = (status) => {
    switch (status) {
        case 'running':
            return (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800">
                    <span className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-pulse" />
                    running
                </span>
            );
        case 'completed':
            return (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800">
                    <CheckCircle size={10} className="text-emerald-500" />
                    completed
                </span>
            );
        case 'failed':
            return (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300 border border-red-200 dark:border-red-800">
                    <AlertCircle size={10} className="text-red-500" />
                    failed
                </span>
            );
        case 'cancelled':
            return (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 border border-gray-200 dark:border-gray-600">
                    cancelled
                </span>
            );
        case 'orphaned':
            return (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 border border-gray-200 dark:border-gray-600" title="The queue says running, but no worker or runner is working on it">
                    no owner
                </span>
            );
        case 'paused':
        case 'awaiting_review':
            return (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 border border-amber-200 dark:border-amber-800/50">
                    {status === 'paused' ? 'paused' : 'awaiting review'}
                </span>
            );
        case 'pending':
        default:
            return (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 border border-amber-200 dark:border-amber-800/50">
                    <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
                    pending
                </span>
            );
    }
};

const isParentJob = (job) => {
    if (!job) return false;
    const parentTypes = ['batch_translate', 'auto_translate', 'simple_pipeline', 'pipeline'];
    if (parentTypes.includes(job.type)) return true;
    if (parentTypes.some(t => typeof job.id === 'string' && job.id.startsWith(`${t}_`))) return true;
    if (Array.isArray(job.sub_jobs) && job.sub_jobs.length > 0) return true;
    if ((job.completed_episodes?.length || 0) + (job.failed_episodes?.length || 0) > 0) return true;
    return false;
};

const getChildTasks = (parentJob, activeJobs = {}) => {
    if (!parentJob) return [];

    const tasksMap = new Map();
    const subJobIds = new Set((parentJob.sub_jobs || []).map(String));

    // 1. Scan activeJobs for children associated with this parent
    Object.values(activeJobs).forEach(job => {
        if (!job) return;
        const sid = String(job.id);
        const bareSid = sid.replace(/^bg_/, '');
        const isChild = job.parentId === parentJob.id ||
                        subJobIds.has(sid) ||
                        subJobIds.has(bareSid);
        if (isChild) {
            const epName = job.episode_name || job.metadata?.episodeName || `Item ${job.id}`;
            tasksMap.set(epName, {
                id: job.id,
                episodeName: epName,
                status: job.status || 'running',
                progress: typeof job.progress === 'number' ? job.progress : 0,
                cues_translated: job.cues_translated ?? null,
                total_cues: job.total_cues ?? job.cues_total ?? null,
                logs: Array.isArray(job.logs) ? job.logs : [],
                hasLogs: Boolean(job.logs && job.logs.length > 0),
                message: job.message || '',
                scene_status: job.scene_status || {},
                jobRef: job
            });
        }
    });

    // 2. Incorporate planned episodes from metadata if present
    const metaEpisodes = parentJob.metadata?.episodes || parentJob.metadata?.episodeNames || [];
    metaEpisodes.forEach(epName => {
        if (!tasksMap.has(epName)) {
            tasksMap.set(epName, {
                id: `ep_${epName}`,
                episodeName: epName,
                status: 'pending',
                progress: 0,
                cues_translated: null,
                total_cues: null,
                logs: [],
                hasLogs: false,
                message: 'Pending in queue',
                scene_status: {},
                jobRef: null
            });
        }
    });

    // 3. Mark completed episodes from parent telemetry
    (parentJob.completed_episodes || []).forEach(epName => {
        const existing = tasksMap.get(epName);
        if (existing) {
            existing.status = 'completed';
            existing.progress = 100;
        } else {
            tasksMap.set(epName, {
                id: `completed_${epName}`,
                episodeName: epName,
                status: 'completed',
                progress: 100,
                cues_translated: null,
                total_cues: null,
                logs: [],
                hasLogs: false,
                message: 'Translation completed',
                scene_status: {},
                jobRef: null
            });
        }
    });

    // 4. Mark failed episodes from parent telemetry
    (parentJob.failed_episodes || []).forEach(epName => {
        const existing = tasksMap.get(epName);
        if (existing) {
            existing.status = 'failed';
        } else {
            tasksMap.set(epName, {
                id: `failed_${epName}`,
                episodeName: epName,
                status: 'failed',
                progress: 0,
                cues_translated: null,
                total_cues: null,
                logs: [],
                hasLogs: false,
                message: 'Translation failed',
                scene_status: {},
                jobRef: null
            });
        }
    });

    // 5. Fallback: if no episode names were available but sub_jobs exist
    if (tasksMap.size === 0 && Array.isArray(parentJob.sub_jobs)) {
        parentJob.sub_jobs.forEach(subId => {
            const sid = String(subId);
            const childJob = activeJobs[sid] || activeJobs[`bg_${sid}`];
            const epName = childJob?.episode_name || childJob?.metadata?.episodeName || `Queue #${sid}`;
            tasksMap.set(sid, {
                id: childJob?.id || `bg_${sid}`,
                episodeName: epName,
                status: childJob?.status || 'pending',
                progress: childJob?.progress || 0,
                cues_translated: childJob?.cues_translated ?? null,
                total_cues: childJob?.total_cues ?? childJob?.cues_total ?? null,
                logs: childJob?.logs || [],
                hasLogs: Boolean(childJob?.logs?.length),
                message: childJob?.message || '',
                scene_status: childJob?.scene_status || {},
                jobRef: childJob || null
            });
        });
    }

    return Array.from(tasksMap.values());
};

const JobProgressWidget = () => {
    const { activeJobs, removeJob, cancelJob, clearAll } = useJobs();
    // The Activity page lists the same live work in full: there the panel waits as its pill instead of
    // covering the queue's rows (and their Cancel buttons); it opens again on the other pages.
    const onActivityPage = useLocation().pathname === '/activity';
    const [isExpanded, setIsExpanded] = useState(!onActivityPage);
    const [lastPage, setLastPage] = useState(onActivityPage);
    if (lastPage !== onActivityPage) {
        setLastPage(onActivityPage);
        setIsExpanded(!onActivityPage);
    }
    const [showLogs, setShowLogs] = useState(null); // jobId to show inline logs for
    const [maximizedLogsJobId, setMaximizedLogsJobId] = useState(null);
    const [maximizedTab, setMaximizedTab] = useState('logs'); // 'logs' | 'ai_response' | 'prompt'
    const [logFilter, setLogFilter] = useState('all'); // 'all' | 'errors' | 'stages'
    const [copyStatus, setCopyStatus] = useState('idle'); // 'idle' | 'copied' | 'error'
    const [isAutoScrollLocked, setIsAutoScrollLocked] = useState(true);
    const [expandedAccordions, setExpandedAccordions] = useState({});
    const logsContainerRef = useRef(null);
    const navigate = useNavigate();

    const jobs = useMemo(() => Object.values(activeJobs || {}), [activeJobs]);

    const knownSubJobIds = useMemo(() => {
        const idSet = new Set();
        jobs.forEach(j => {
            if (Array.isArray(j?.sub_jobs)) {
                j.sub_jobs.forEach(subId => {
                    const sid = String(subId);
                    idSet.add(sid);
                    idSet.add(`bg_${sid}`);
                });
            }
        });
        return idSet;
    }, [jobs]);

    const visibleJobs = useMemo(() => {
        return jobs.filter(job => job && !job.parentId && !job.parent_job_id && !knownSubJobIds.has(String(job.id)));
    }, [jobs, knownSubJobIds]);

    const isActive = (job) => job && ['running', 'pending', 'paused', 'awaiting_review'].includes(job.status);
    const liveCount = useMemo(
        () => visibleJobs.filter(job => ['running', 'pending', 'paused', 'awaiting_review', 'orphaned'].includes(job.status)).length,
        [visibleJobs]
    );

    // An orphaned queue row (running with no owner): put it back in the queue so it is translated again.
    const releaseOrphan = async (job) => {
        const queueId = String(job.id).replace(/^bg_/, '');
        try {
            await apiClient.releaseQueueItem(queueId);
        } catch (err) {
            console.error(`Failed to release queue item ${queueId}`, err);
        }
    };

    const isAccordionExpanded = (jobId) => {
        if (expandedAccordions[jobId] !== undefined) {
            return expandedAccordions[jobId];
        }
        const job = activeJobs?.[jobId];
        return job ? ['running', 'pending', 'awaiting_review'].includes(job.status) : true;
    };

    const toggleAccordion = (jobId) => {
        setExpandedAccordions(prev => ({
            ...prev,
            [jobId]: !isAccordionExpanded(jobId)
        }));
    };

    const handleInspectChildLogs = (child, parentJob) => {
        if (child.id && activeJobs[child.id]) {
            setMaximizedLogsJobId(child.id);
        } else {
            setMaximizedLogsJobId(child.id || `ep_${child.episodeName}_${parentJob.id}`);
        }
    };

    // Resolve job for modal overlay
    const activeModalJob = useMemo(() => {
        if (!maximizedLogsJobId) return null;
        if (activeJobs?.[maximizedLogsJobId]) {
            return activeJobs[maximizedLogsJobId];
        }
        for (const pJob of visibleJobs) {
            const childTasks = getChildTasks(pJob, activeJobs);
            const match = childTasks.find(c =>
                c.id === maximizedLogsJobId ||
                `ep_${c.episodeName}_${pJob.id}` === maximizedLogsJobId ||
                `ep_${c.episodeName}` === maximizedLogsJobId ||
                `completed_${c.episodeName}` === maximizedLogsJobId ||
                `failed_${c.episodeName}` === maximizedLogsJobId
            );
            if (match) {
                const logs = match.logs && match.logs.length > 0
                    ? match.logs
                    : (pJob.logs || []).filter(l => l.includes(match.episodeName));
                return {
                    id: match.id,
                    project_name: pJob.project_name || pJob.metadata?.projectId,
                    episode_name: match.episodeName,
                    description: `${pJob.project_name || 'Project'} — ${match.episodeName}`,
                    status: match.status,
                    progress: match.progress,
                    logs: logs,
                    scene_status: match.scene_status || {},
                    cues_translated: match.cues_translated,
                    cues_total: match.total_cues,
                    total_cues: match.total_cues,
                    ai_response: match.jobRef?.ai_response || null,
                    prompt: match.jobRef?.prompt || null,
                };
            }
        }
        return null;
    }, [maximizedLogsJobId, activeJobs, visibleJobs]);

    // Categorized logs for activeModalJob
    const { allLogs, errorLogs, stageLogs, displayedLogs, totalRawCount } = useMemo(() => {
        const rawLogs = activeModalJob?.logs || [];
        const windowedLogs = rawLogs.length > 500 ? rawLogs.slice(-500) : rawLogs;
        const errors = windowedLogs.filter(line =>
            /(?:failed|failure|error|exception|warning|incomplete|limit|quota|daily limit|cancelled|retry|retrying|429|500|✗)/i.test(line)
        );
        const stages = windowedLogs.filter(line =>
            /(?:starting|initializing|step\s*\d|stage|phase|pipeline|scene\s*\d|scenes|translating|retranslating|repairing|repair pass|scan|context guide|glossary|research|export|saved|checkpoint|completed|finished|successfully|✓)/i.test(line)
        );

        let displayed = windowedLogs;
        if (logFilter === 'errors') displayed = errors;
        else if (logFilter === 'stages') displayed = stages;

        return { allLogs: windowedLogs, errorLogs: errors, stageLogs: stages, displayedLogs: displayed, totalRawCount: rawLogs.length };
    }, [activeModalJob?.logs, logFilter]);

    // Auto-scroll handler
    const handleScroll = (e) => {
        const { scrollTop, scrollHeight, clientHeight } = e.currentTarget;
        const distanceFromBottom = scrollHeight - (scrollTop + clientHeight);
        const isAtBottom = distanceFromBottom <= 35;
        if (isAtBottom !== isAutoScrollLocked) {
            setIsAutoScrollLocked(isAtBottom);
        }
    };

    const handleJumpToNewest = () => {
        if (logsContainerRef.current) {
            logsContainerRef.current.scrollTo({
                top: logsContainerRef.current.scrollHeight,
                behavior: 'smooth'
            });
        }
        setIsAutoScrollLocked(true);
    };

    useEffect(() => {
        if (isAutoScrollLocked && logsContainerRef.current) {
            logsContainerRef.current.scrollTop = logsContainerRef.current.scrollHeight;
        }
    }, [displayedLogs, isAutoScrollLocked, maximizedTab]);

    const handleCopyLogs = async () => {
        try {
            let copyText = '';
            if (maximizedTab === 'ai_response') {
                copyText = activeModalJob?.ai_response || '';
            } else if (maximizedTab === 'prompt') {
                copyText = activeModalJob?.prompt || '';
            } else {
                copyText = displayedLogs.join('\n');
            }
            await navigator.clipboard.writeText(copyText);
            setCopyStatus('copied');
            setTimeout(() => setCopyStatus('idle'), 2000);
        } catch (err) {
            console.error('Failed to copy logs', err);
            setCopyStatus('error');
            setTimeout(() => setCopyStatus('idle'), 2000);
        }
    };

    const handleDownloadLogs = () => {
        if (!activeModalJob) return;
        const job = activeModalJob;
        const timestamp = new Date().toISOString();
        const header = [
            '================================================================================',
            `OMBISUB JOB LOG DUMP — ${timestamp}`,
            '================================================================================',
            `Job ID:          ${job.id}`,
            `Project:         ${job.project_name || 'N/A'}`,
            `Episode:         ${job.episode_name || 'N/A'}`,
            `Status:          ${job.status}`,
            `Progress:        ${job.progress}%`,
            `Stage:           ${job.pipeline_stage || 'N/A'}`,
            `Cues Translated: ${job.cues_translated != null ? `${job.cues_translated}/${job.total_cues || job.cues_total || '?'}` : 'N/A'}`,
            `ETA:             ${job.eta_seconds != null ? `${Math.ceil(job.eta_seconds)}s` : 'N/A'}`,
            '================================================================================',
            '',
            '--- LOG STREAM ---',
            ''
        ].join('\n');

        let content = header + (job.logs || []).join('\n');

        if (job.prompt) {
            content += '\n\n' + [
                '================================================================================',
                '--- LAST PROMPT ---',
                '================================================================================',
                '',
                job.prompt
            ].join('\n');
        }

        if (job.ai_response) {
            content += '\n\n' + [
                '================================================================================',
                '--- LAST AI RESPONSE ---',
                '================================================================================',
                '',
                job.ai_response
            ].join('\n');
        }

        const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const sanitizedId = String(job.id).replace(/[^a-zA-Z0-9_-]/g, '_');
        a.href = url;
        a.download = `${sanitizedId}_logs.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    if (visibleJobs.length === 0) return null;

    return (
        <div className="fixed bottom-4 right-4 z-50 w-96 flex flex-col gap-2">
            <AnimatePresence>
                {isExpanded && (
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: 20 }}
                        className="bg-white dark:bg-gray-800 rounded-lg shadow-2xl border border-gray-200 dark:border-gray-700 overflow-hidden max-h-[80vh] flex flex-col"
                    >
                        <div className="p-3 bg-gray-50 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center">
                            <h3 className="font-semibold text-sm text-gray-700 dark:text-gray-300 flex items-center gap-2">
                                <Activity size={16} className="text-indigo-500" />
                                Active Tasks ({liveCount})
                            </h3>
                            <div className="flex items-center gap-2">
                                <button
                                    type="button"
                                    onClick={clearAll}
                                    className="inline-flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
                                    title="Clear every card (the work itself carries on)"
                                >
                                    <Trash2 size={13} /> Clear
                                </button>
                                <button onClick={() => setIsExpanded(false)} className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200" title="Minimise">
                                    <ChevronDown size={16} />
                                </button>
                            </div>
                        </div>

                        <div className="overflow-y-auto p-2 space-y-2">
                            {visibleJobs.map(job => (
                                <div key={job.id} className={`bg-gray-50 dark:bg-gray-700/50 rounded-lg p-3 border border-gray-100 dark:border-gray-600 ${job.status === 'orphaned' ? 'opacity-60' : ''}`}>
                                    <div className="flex justify-between items-start mb-2">
                                        <div>
                                            <p className="font-medium text-sm text-gray-800 dark:text-gray-200">
                                                {(() => {
                                                    const pName = job.project_name || job.metadata?.projectId;
                                                    const epName = job.episode_name || job.metadata?.episodeName;
                                                    if (epName && pName) {
                                                        return (
                                                            <span className="flex flex-col">
                                                                <span className="text-[10px] uppercase tracking-wider text-indigo-500 font-bold">{pName}</span>
                                                                <span>{epName}</span>
                                                            </span>
                                                        );
                                                    }
                                                    return job.description;
                                                })()}
                                            </p>
                                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{job.message}</p>
                                            {job.status === 'orphaned' && <div className="mt-1">{renderStatusPill('orphaned')}</div>}
                                        </div>
                                        <div className="flex items-center gap-2">
                                            {job.status === 'completed' && <CheckCircle size={16} className="text-green-500" />}
                                            {job.status === 'failed' && <AlertCircle size={16} className="text-red-500" />}
                                            {job.status === 'cancelled' && <X size={16} className="text-gray-400" />}
                                            {job.status === 'running' && (
                                                <div className="relative">
                                                    <div className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                                                    {(job.logs || []).some(l => l.includes("Reasoning:")) && (
                                                        <Sparkles size={10} className="absolute -top-1 -right-1 text-yellow-500 animate-pulse" />
                                                    )}
                                                </div>
                                            )}
                                            {isActive(job) && (
                                                <button onClick={() => cancelJob(job.id)} className="flex items-center gap-1 text-xs text-red-400 hover:text-red-600 font-medium px-2 py-1 rounded bg-red-50 dark:bg-red-900/20" title="Cancel Job">
                                                    Cancel
                                                </button>
                                            )}
                                            {job.status === 'orphaned' && (
                                                <button onClick={() => releaseOrphan(job)} className="flex items-center gap-1 text-xs text-gray-600 hover:text-gray-800 dark:text-gray-300 dark:hover:text-white font-medium px-2 py-1 rounded bg-gray-100 dark:bg-gray-700" title="Release this queue item so it can be picked up again">
                                                    Release
                                                </button>
                                            )}
                                            <button
                                                onClick={() => removeJob(job.id)}
                                                className="text-gray-400 hover:text-gray-600 ml-1"
                                                title="Dismiss (hides the card; the work carries on)"
                                            >
                                                <X size={16} />
                                            </button>
                                        </div>
                                    </div>

                                    {/* Progress Bar */}
                                    <div className="w-full bg-gray-200 dark:bg-gray-600 rounded-full h-1.5 mb-2 overflow-hidden">
                                        <div
                                            className={`h-full rounded-full transition-all duration-500 ${job.status === 'failed' ? 'bg-red-500' :
                                                job.status === 'completed' ? 'bg-green-500' :
                                                job.status === 'orphaned' ? 'bg-gray-400' : 'bg-indigo-500'
                                                }`}
                                            style={{ width: `${job.progress}%` }}
                                        />
                                    </div>

                                    <div className="flex items-center justify-between mt-1">
                                        {/* Logs Toggle */}
                                        <button
                                            onClick={() => setShowLogs(showLogs === job.id ? null : job.id)}
                                            className="text-xs text-indigo-600 hover:text-indigo-800 dark:text-indigo-400 dark:hover:text-indigo-300 flex items-center gap-1"
                                        >
                                            <Terminal size={12} />
                                            {showLogs === job.id ? 'Hide Logs' : 'Show Logs'}
                                        </button>

                                        {/* Navigation Link */}
                                        {job.metadata?.link && (
                                            <button
                                                onClick={() => navigate(job.metadata.link, { state: { highlightJobId: job.id } })}
                                                className="text-xs text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 flex items-center gap-1"
                                            >
                                                <ExternalLink size={12} />
                                                View
                                            </button>
                                        )}
                                    </div>

                                    {/* Expandable Episode Tasks Accordion for Parent Jobs */}
                                    {isParentJob(job) && (() => {
                                        const childTasks = getChildTasks(job, activeJobs);
                                        const completedCount = childTasks.filter(c => c.status === 'completed').length;
                                        const isAccordionOpen = isAccordionExpanded(job.id);

                                        return (
                                            <div className="mt-2.5 mb-1.5">
                                                <button
                                                    type="button"
                                                    onClick={() => toggleAccordion(job.id)}
                                                    className="w-full px-2 py-1.5 rounded-md bg-gray-100 dark:bg-gray-700/60 hover:bg-gray-200/70 dark:hover:bg-gray-700 transition-colors flex items-center justify-between text-left group"
                                                >
                                                    <div className="flex items-center gap-1.5 text-xs font-semibold text-gray-700 dark:text-gray-200">
                                                        <motion.div
                                                            animate={{ rotate: isAccordionOpen ? 180 : 0 }}
                                                            transition={{ duration: 0.2 }}
                                                        >
                                                            <ChevronDown size={14} className="text-gray-500 group-hover:text-indigo-600 transition-colors" />
                                                        </motion.div>
                                                        <span className="flex items-center gap-1">
                                                            Episode Tasks
                                                            <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-indigo-100 dark:bg-indigo-900/50 text-indigo-700 dark:text-indigo-300 font-bold ml-1">
                                                                {completedCount} / {childTasks.length}
                                                            </span>
                                                        </span>
                                                    </div>
                                                    <span className="text-[10px] text-gray-500 dark:text-gray-400">
                                                        {isAccordionOpen ? 'Collapse' : 'Expand'}
                                                    </span>
                                                </button>

                                                <AnimatePresence>
                                                    {isAccordionOpen && (
                                                        <motion.div
                                                            initial={{ height: 0, opacity: 0 }}
                                                            animate={{ height: 'auto', opacity: 1 }}
                                                            exit={{ height: 0, opacity: 0 }}
                                                            transition={{ duration: 0.2 }}
                                                            className="overflow-hidden space-y-1.5 mt-1.5 pl-1.5 border-l-2 border-indigo-300 dark:border-indigo-800 max-h-60 overflow-y-auto"
                                                        >
                                                            {childTasks.length === 0 ? (
                                                                <p className="text-[11px] text-gray-400 italic py-1">No child episode tasks found.</p>
                                                            ) : (
                                                                childTasks.map(child => (
                                                                    <div
                                                                        key={child.id || child.episodeName}
                                                                        className="p-2 rounded bg-white dark:bg-gray-800/90 border border-gray-200 dark:border-gray-700/80 shadow-xs flex flex-col gap-1.5"
                                                                    >
                                                                        {/* Top Row: Episode Name & Status Pill */}
                                                                        <div className="flex items-center justify-between gap-2">
                                                                            <span className="text-xs font-medium text-gray-800 dark:text-gray-200 truncate" title={child.episodeName}>
                                                                                {child.episodeName}
                                                                            </span>
                                                                            {renderStatusPill(child.status)}
                                                                        </div>

                                                                        {/* Middle Row: Mini Progress Bar */}
                                                                        <div className="w-full bg-gray-100 dark:bg-gray-700 rounded-full h-1 overflow-hidden">
                                                                            <div
                                                                                className={`h-full transition-all duration-300 ${
                                                                                    child.status === 'failed' ? 'bg-red-500' :
                                                                                    child.status === 'completed' ? 'bg-emerald-500' :
                                                                                    'bg-indigo-500'
                                                                                }`}
                                                                                style={{ width: `${child.status === 'completed' ? 100 : (child.progress || 0)}%` }}
                                                                            />
                                                                        </div>

                                                                        {/* Bottom Row: Cue Counter & Inspect Logs Button */}
                                                                        <div className="flex items-center justify-between text-[10px] text-gray-500 dark:text-gray-400 pt-0.5">
                                                                            <div className="flex items-center gap-1.5 font-mono">
                                                                                {child.cues_translated != null && child.total_cues != null ? (
                                                                                    <span>
                                                                                        <strong className="text-gray-700 dark:text-gray-200">{child.cues_translated}</strong>
                                                                                        /{child.total_cues} cues
                                                                                    </span>
                                                                                ) : (
                                                                                    <span>{child.message || (child.status === 'completed' ? 'Finished' : 'In queue')}</span>
                                                                                )}
                                                                            </div>

                                                                            <button
                                                                                type="button"
                                                                                onClick={() => handleInspectChildLogs(child, job)}
                                                                                className="text-[10px] font-sans font-medium text-indigo-600 hover:text-indigo-800 dark:text-indigo-400 dark:hover:text-indigo-300 flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-indigo-50 dark:hover:bg-indigo-900/30 transition-colors"
                                                                                title={`Inspect logs for ${child.episodeName}`}
                                                                            >
                                                                                <Terminal size={10} />
                                                                                Logs
                                                                            </button>
                                                                        </div>
                                                                    </div>
                                                                ))
                                                            )}
                                                        </motion.div>
                                                    )}
                                                </AnimatePresence>
                                            </div>
                                        );
                                    })()}

                                    {/* Cues Progress & ETA */}
                                    {(job.cues_total > 0 || (job.eta_seconds != null && job.eta_seconds > 0)) && (
                                        <div className="mt-1 mb-1 flex items-center justify-between text-[10px] text-gray-500">
                                            {job.cues_total > 0 && (
                                                <span>Cues: <strong className="text-gray-700 dark:text-gray-300">{job.cues_translated || 0}/{job.cues_total}</strong></span>
                                            )}
                                            {job.eta_seconds != null && job.eta_seconds > 0 && (
                                                <span>ETA: <strong className="text-indigo-600 dark:text-indigo-400">{Math.ceil(job.eta_seconds)}s</strong></span>
                                            )}
                                        </div>
                                    )}

                                    {/* Daily Limit Warning */}
                                    {job.daily_limit_reached && (
                                        <div className="mt-2 mb-1 p-2 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700 rounded-md flex items-start gap-2">
                                            <AlertCircle size={14} className="text-amber-500 shrink-0 mt-0.5" />
                                            <div className="text-[10px] text-amber-700 dark:text-amber-400">
                                                <p className="font-bold">Daily API Limit Reached</p>
                                                <p>Remaining episodes queued. Check settings for RPM/RPD limits.</p>
                                            </div>
                                        </div>
                                    )}

                                    {/* Scenes Progress (Numerically Sorted with Enhanced Colors) */}
                                    {job.scene_status && Object.keys(job.scene_status).length > 0 && (
                                        <div className="mt-2 flex flex-wrap gap-1">
                                            {getSortedScenes(job.scene_status).map(([sceneName, status]) => (
                                                <span
                                                    key={sceneName}
                                                    title={`${sceneName}: ${status}`}
                                                    className={`text-[10px] px-1.5 py-0.5 rounded border transition-all ${getSceneColor(status)}`}
                                                >
                                                    {sceneName.replace('Scene ', 'S')}
                                                </span>
                                            ))}
                                        </div>
                                    )}

                                    {/* Inline Logs View */}
                                    <AnimatePresence>
                                        {showLogs === job.id && (
                                            <motion.div
                                                initial={{ height: 0, opacity: 0 }}
                                                animate={{ height: 'auto', opacity: 1 }}
                                                exit={{ height: 0, opacity: 0 }}
                                                className="mt-2 bg-black text-gray-300 p-2 rounded text-xs font-mono overflow-hidden"
                                            >
                                                <div className="flex justify-between items-center border-b border-gray-800 pb-1 mb-1 bg-black shrink-0">
                                                    <span className="text-[10px] text-gray-500 uppercase tracking-wider">Console Output</span>
                                                    <button
                                                        onClick={() => {
                                                            setMaximizedLogsJobId(job.id);
                                                            setMaximizedTab('logs');
                                                        }}
                                                        className="text-[10px] text-indigo-400 hover:text-indigo-300 flex items-center gap-0.5"
                                                    >
                                                        <Maximize2 size={10} /> Maximize
                                                    </button>
                                                </div>
                                                <div className="max-h-32 overflow-y-auto space-y-1">
                                                    {(job.logs || []).slice(-30).map((log, i) => (
                                                        <div key={i}>{renderColorizedLog(log)}</div>
                                                    ))}
                                                </div>
                                            </motion.div>
                                        )}
                                    </AnimatePresence>
                                </div>
                            ))}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            {!isExpanded && (
                <motion.button
                    initial={{ scale: 0.9, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    onClick={() => setIsExpanded(true)}
                    className="bg-white dark:bg-gray-800 p-3 rounded-full shadow-lg border border-gray-200 dark:border-gray-700 flex items-center gap-2 self-end hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                >
                    <Activity size={20} className="text-indigo-500" />
                    <span className="font-semibold text-sm text-gray-700 dark:text-gray-300">{liveCount}</span>
                </motion.button>
            )}

            {/* Maximized Live Log Console Modal */}
            <AnimatePresence>
                {maximizedLogsJobId && activeModalJob && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="fixed inset-0 bg-black/60 backdrop-blur-sm z-[100] flex items-center justify-center p-4 md:p-8"
                    >
                        <motion.div
                            initial={{ scale: 0.95, opacity: 0 }}
                            animate={{ scale: 1, opacity: 1 }}
                            exit={{ scale: 0.95, opacity: 0 }}
                            className="bg-gray-950 text-gray-100 rounded-xl shadow-2xl border border-gray-800 w-full max-w-5xl h-[85vh] flex flex-col overflow-hidden font-mono"
                        >
                            {/* Header Bar */}
                            <div className="p-3.5 bg-gray-900 border-b border-gray-800 flex flex-wrap justify-between items-center gap-3 shrink-0">
                                <div className="flex items-center gap-3">
                                    <Terminal size={18} className="text-indigo-400 shrink-0" />
                                    <div>
                                        <span className="font-semibold text-sm text-gray-100 font-sans">
                                            {activeModalJob.episode_name || activeModalJob.metadata?.episodeName || activeModalJob.description || 'Job Console'}
                                        </span>
                                        {activeModalJob.project_name && (
                                            <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-indigo-950/80 text-indigo-300 border border-indigo-700/50 font-sans uppercase tracking-wider font-bold">
                                                {activeModalJob.project_name}
                                            </span>
                                        )}
                                    </div>
                                </div>

                                {/* Mode Tabs (Logs / AI Response / Prompt) */}
                                <div className="flex items-center bg-gray-950 rounded-lg p-0.5 border border-gray-800 text-xs font-sans">
                                    <button
                                        onClick={() => setMaximizedTab('logs')}
                                        className={`px-3 py-1 rounded-md transition-colors ${maximizedTab === 'logs' ? 'bg-indigo-600 text-white font-medium' : 'text-gray-400 hover:text-gray-200'}`}
                                    >
                                        Logs ({activeModalJob.logs?.length || 0})
                                    </button>
                                    {activeModalJob.ai_response && (
                                        <button
                                            onClick={() => setMaximizedTab('ai_response')}
                                            className={`px-3 py-1 rounded-md transition-colors ${maximizedTab === 'ai_response' ? 'bg-indigo-600 text-white font-medium' : 'text-gray-400 hover:text-gray-200'}`}
                                        >
                                            AI Response
                                        </button>
                                    )}
                                    {activeModalJob.prompt && (
                                        <button
                                            onClick={() => setMaximizedTab('prompt')}
                                            className={`px-3 py-1 rounded-md transition-colors ${maximizedTab === 'prompt' ? 'bg-indigo-600 text-white font-medium' : 'text-gray-400 hover:text-gray-200'}`}
                                        >
                                            Prompt
                                        </button>
                                    )}
                                </div>

                                {/* Action Buttons: Copy & Download */}
                                <div className="flex items-center gap-2">
                                    <button
                                        onClick={handleCopyLogs}
                                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-gray-800 hover:bg-gray-700 text-gray-200 rounded-lg transition-colors border border-gray-700 font-sans"
                                        title="Copy logs to clipboard"
                                    >
                                        {copyStatus === 'copied' ? (
                                            <>
                                                <Check size={13} className="text-emerald-400" />
                                                <span className="text-emerald-400 font-medium">Copied!</span>
                                            </>
                                        ) : (
                                            <>
                                                <Copy size={13} />
                                                <span>Copy {maximizedTab === 'ai_response' ? 'AI Response' : maximizedTab === 'prompt' ? 'Prompt' : 'Logs'}</span>
                                            </>
                                        )}
                                    </button>
                                    <button
                                        onClick={handleDownloadLogs}
                                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-gray-800 hover:bg-gray-700 text-gray-200 rounded-lg transition-colors border border-gray-700 font-sans"
                                        title="Download formatted logs (.txt)"
                                    >
                                        <Download size={13} className="text-indigo-400" />
                                        <span>Download Logs (.txt)</span>
                                    </button>
                                    <button
                                        onClick={() => {
                                            setMaximizedLogsJobId(null);
                                            setMaximizedTab('logs');
                                        }}
                                        className="p-1.5 hover:bg-gray-800 rounded-lg transition-colors text-gray-400 hover:text-gray-200"
                                    >
                                        <X size={18} />
                                    </button>
                                </div>
                            </div>

                            {/* Sub-Header: Filter Tabs & Telemetry Summary (when on logs tab) */}
                            {maximizedTab === 'logs' && (
                                <div className="px-4 py-2 bg-gray-900/60 border-b border-gray-800/80 flex flex-wrap items-center justify-between gap-2 shrink-0 font-sans">
                                    {/* Category Filter Tabs */}
                                    <div className="flex items-center gap-1 bg-gray-950 p-1 rounded-lg border border-gray-800 text-xs">
                                        <button
                                            onClick={() => setLogFilter('all')}
                                            className={`px-2.5 py-0.5 rounded-md transition-colors ${logFilter === 'all' ? 'bg-gray-800 text-white font-medium' : 'text-gray-400 hover:text-gray-200'}`}
                                        >
                                            All ({allLogs.length})
                                        </button>
                                        <button
                                            onClick={() => setLogFilter('errors')}
                                            className={`px-2.5 py-0.5 rounded-md transition-colors flex items-center gap-1 ${logFilter === 'errors' ? 'bg-rose-950 text-rose-200 font-medium border border-rose-800/50' : 'text-gray-400 hover:text-gray-200'}`}
                                        >
                                            Errors & Warnings ({errorLogs.length})
                                        </button>
                                        <button
                                            onClick={() => setLogFilter('stages')}
                                            className={`px-2.5 py-0.5 rounded-md transition-colors flex items-center gap-1 ${logFilter === 'stages' ? 'bg-blue-950 text-blue-200 font-medium border border-blue-800/50' : 'text-gray-400 hover:text-gray-200'}`}
                                        >
                                            Stages ({stageLogs.length})
                                        </button>
                                    </div>

                                    {/* Telemetry Summary */}
                                    <div className="flex items-center gap-3 text-xs text-gray-400">
                                        {activeModalJob.cues_total > 0 && (
                                            <span>Cues: <strong className="text-gray-200">{activeModalJob.cues_translated || 0}/{activeModalJob.cues_total}</strong></span>
                                        )}
                                        {activeModalJob.eta_seconds != null && activeModalJob.eta_seconds > 0 && (
                                            <span>ETA: <strong className="text-indigo-400">{Math.ceil(activeModalJob.eta_seconds)}s</strong></span>
                                        )}
                                    </div>
                                </div>
                            )}

                            {/* Persistent Scene Status Bar in Console */}
                            {maximizedTab === 'logs' && getSortedScenes(activeModalJob.scene_status).length > 0 && (
                                <div className="px-4 py-2 bg-gray-950 border-b border-gray-800/80 flex items-center gap-2 overflow-x-auto shrink-0 font-sans text-xs">
                                    <span className="text-[11px] font-semibold text-gray-400 shrink-0">
                                        Scenes ({getSortedScenes(activeModalJob.scene_status).filter(s => s[1] === 'completed').length}/{getSortedScenes(activeModalJob.scene_status).length}):
                                    </span>
                                    <div className="flex items-center gap-1.5 flex-wrap">
                                        {getSortedScenes(activeModalJob.scene_status).map(([sceneName, status]) => (
                                            <span
                                                key={sceneName}
                                                title={`${sceneName}: ${status}`}
                                                className={`text-[10px] px-2 py-0.5 rounded-md font-mono border transition-all ${getSceneColor(status)}`}
                                            >
                                                {sceneName.replace('Scene ', 'S')}
                                            </span>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Console Log Area with Auto-Scroll Detection & Pause Banner */}
                            <div className="relative flex-1 min-h-0">
                                <div
                                    ref={logsContainerRef}
                                    onScroll={handleScroll}
                                    className="h-full p-4 overflow-y-auto space-y-1.5 text-xs select-text font-mono"
                                >
                                    {maximizedTab === 'logs' && (
                                        <>
                                            {totalRawCount > 500 && (
                                                <div className="text-gray-500 italic text-[11px] pb-2 border-b border-gray-800 mb-2">
                                                    Showing latest 500 of {totalRawCount} log lines
                                                </div>
                                            )}
                                            {displayedLogs.length > 0 ? (
                                                displayedLogs.map((log, i) => (
                                                    <div key={i} className="whitespace-pre-wrap leading-relaxed">
                                                        {renderColorizedLog(log)}
                                                    </div>
                                                ))
                                            ) : (
                                                <div className="h-full flex flex-col items-center justify-center text-gray-500 font-sans text-xs py-10">
                                                    <p>No log entries match the selected filter ({logFilter}).</p>
                                                </div>
                                            )}
                                        </>
                                    )}

                                    {maximizedTab === 'ai_response' && (
                                        <div className="whitespace-pre-wrap text-emerald-300 bg-gray-900/60 p-4 rounded-lg border border-gray-800 leading-relaxed font-mono">
                                            {activeModalJob.ai_response || 'No AI response recorded.'}
                                        </div>
                                    )}

                                    {maximizedTab === 'prompt' && (
                                        <div className="whitespace-pre-wrap text-cyan-300 bg-gray-900/60 p-4 rounded-lg border border-gray-800 leading-relaxed font-mono">
                                            {activeModalJob.prompt || 'No prompt recorded.'}
                                        </div>
                                    )}
                                </div>

                                {/* Auto-Scroll Paused Floating Banner */}
                                <AnimatePresence>
                                    {maximizedTab === 'logs' && !isAutoScrollLocked && (
                                        <motion.button
                                            initial={{ opacity: 0, y: 15, scale: 0.95 }}
                                            animate={{ opacity: 1, y: 0, scale: 1 }}
                                            exit={{ opacity: 0, y: 15, scale: 0.95 }}
                                            onClick={handleJumpToNewest}
                                            className="absolute bottom-4 left-1/2 -translate-x-1/2 z-20 flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-sans font-medium shadow-2xl backdrop-blur-md border border-indigo-400/40 transition-all cursor-pointer group"
                                        >
                                            <ArrowDown size={14} className="group-hover:translate-y-0.5 transition-transform text-indigo-200 animate-bounce" />
                                            <span>Auto-scroll paused — Click to jump to newest</span>
                                        </motion.button>
                                    )}
                                </AnimatePresence>
                            </div>
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
};

export default JobProgressWidget;
