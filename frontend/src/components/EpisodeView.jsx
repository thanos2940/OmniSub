import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { api } from '../api';
import EditorView from './EditorView';
import { ArrowLeft, Sparkles, Languages } from 'lucide-react';
import { useJobs } from '../context/JobContext';
import { useToast } from '../context/ToastContext';

const EpisodeView = () => {
    const { projectName, episodeName } = useParams();
    const { addJob, activeJobs } = useJobs();
    const toast = useToast();
    const [data, setData] = useState([]);
    const [project, setProject] = useState(null);
    const [glossary, setGlossary] = useState({ terms: [] });
    const [loading, setLoading] = useState(true);
    const [processing, setProcessing] = useState(false);
    const [originalFilename, setOriginalFilename] = useState(null);
    const handledJobsRef = React.useRef(new Set());

    useEffect(() => {
        loadData();
    }, [projectName, episodeName]);

    // Watch for completed translation jobs
    useEffect(() => {
        const jobs = Object.values(activeJobs);
        const completedTranslation = jobs.find(job =>
            job.status === 'completed' &&
            job.type === 'translate_episode' &&
            job.metadata?.episodeName === episodeName
        );

        if (completedTranslation && !handledJobsRef.current.has(completedTranslation.id)) {
            loadData();
            handledJobsRef.current.add(completedTranslation.id);
        }
    }, [activeJobs, episodeName]);

    // Live View Polling: Fetch data progressively while a job is running
    useEffect(() => {
        let interval;
        const activeJob = Object.values(activeJobs).find(job => 
            job.status === 'running' && 
            job.type === 'translate_episode' && 
            job.metadata?.episodeName === episodeName
        );

        if (activeJob) {
            console.log("Translation job active, starting live-view polling...");
            interval = setInterval(() => {
                loadData(true); // Silent reload
            }, 3000);
        }

        return () => {
            if (interval) clearInterval(interval);
        };
    }, [activeJobs, episodeName]);

    const loadData = async (silent = false) => {
        if (!episodeName || !projectName) {
            console.error("Missing projectName or episodeName");
            if (!silent) setLoading(false);
            return;
        }

        try {
            if (!silent) setLoading(true);
            const [epRes, projRes] = await Promise.all([
                api.getEpisode(projectName, episodeName),
                api.getProject(projectName)
            ]);
            setData(epRes.data.data);
            setOriginalFilename(epRes.data.metadata?.original_filename);
            setProject(projRes.data);
            setGlossary(projRes.data.glossary || { terms: [] });
        } catch (err) {
            console.error("Failed to load episode data", err);
        } finally {
            if (!silent) setLoading(false);
        }
    };

    const handleEnhanceGlossary = async () => {
        setProcessing(true);
        try {
            await api.enhanceGlossary(projectName, { episode_names: [episodeName] });
            const projRes = await api.getProject(projectName);
            setProject(projRes.data);
            setGlossary(projRes.data.glossary || { terms: [] });
            toast.success('Glossary enhanced with terms from this episode!');
        } catch (err) {
            console.error('Failed to enhance glossary', err);
            toast.error('Failed to enhance glossary');
        } finally {
            setProcessing(false);
        }
    };

    const handleTranslate = async () => {
        try {
            const settings = project?.settings || {};
            const translationModel = settings.translation_model || 'gemini-flash-latest';
            const res = await api.translateEpisode(projectName, episodeName, translationModel);
            addJob(res.data.job_id, 'translate_episode', `Translating ${episodeName}`, { projectId: projectName, episodeName });
        } catch (err) {
            console.error('Failed to start translation', err);
            toast.error('Failed to start translation');
        }
    };

    const activeJob = Object.values(activeJobs).find(job => 
        job.status === 'running' && 
        job.type === 'translate_episode' && 
        job.metadata?.episodeName === episodeName
    );

    if (loading) return <div className="h-screen flex items-center justify-center text-gray-500">Loading...</div>;

    return (
        <div className="h-screen flex flex-col bg-slate-50 dark:bg-gray-900">
            {/* Header (Already updated in previous step) */}
            {/* ... */}
            <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 px-6 py-4 flex flex-col gap-2 shrink-0">
                <div className="flex justify-between items-center">
                    <div className="flex items-center gap-4">
                        <Link to={`/project/${encodeURIComponent(projectName)}`} className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200">
                            <ArrowLeft size={20} />
                        </Link>
                        <div>
                            <h1 className="text-lg font-bold text-gray-900 dark:text-white">{episodeName}</h1>
                            <p className="text-sm text-gray-500 dark:text-gray-400">{project?.show_name}</p>
                        </div>
                    </div>
                    <div className="flex gap-3">
                        <button
                            onClick={handleEnhanceGlossary}
                            disabled={processing || !!activeJob}
                            className="flex items-center gap-2 px-4 py-2 bg-purple-100 text-purple-700 rounded-lg hover:bg-purple-200 transition-colors text-sm font-medium disabled:opacity-50"
                        >
                            <Sparkles size={18} />
                            Enhance Glossary
                        </button>
                        <button
                            onClick={handleTranslate}
                            disabled={processing || !!activeJob}
                            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors text-sm font-medium disabled:opacity-50 shadow-lg shadow-indigo-500/20"
                        >
                            <Languages size={18} />
                            Translate
                        </button>
                    </div>
                </div>

                {/* Inline Progress Bar */}
                {activeJob && (
                    <div className="mt-2 space-y-1">
                        <div className="flex justify-between text-[10px] font-bold uppercase tracking-tight text-indigo-500">
                            <span>{activeJob.message}</span>
                            <span>{Math.round(activeJob.progress)}%</span>
                        </div>
                        <div className="w-full bg-indigo-100 dark:bg-indigo-900/30 rounded-full h-1.5 overflow-hidden">
                            <div 
                                className="bg-indigo-600 h-full transition-all duration-700 ease-out shadow-[0_0_10px_rgba(79,70,229,0.5)]"
                                style={{ width: `${activeJob.progress}%` }}
                            />
                        </div>
                    </div>
                )}
            </header>

            {/* Editor */}
            <div className="flex-1 overflow-hidden">
                <EditorView
                    data={data}
                    glossary={glossary}
                    project={project}
                    projectName={projectName}
                    episodeName={episodeName}
                    originalFilename={originalFilename}
                    onDataUpdate={setData}
                    isLoading={!!activeJob}
                />
            </div>
        </div>
    );
};

export default EpisodeView;
