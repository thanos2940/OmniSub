import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { api } from '../api';
import EditorView from './EditorView';
import { ArrowLeft, Sparkles, Languages } from 'lucide-react';
import TranslationModal from './TranslationModal';
import { useJobs } from '../context/JobContext';

const EpisodeView = () => {
    const { projectName, episodeName } = useParams();
    const { addJob, activeJobs } = useJobs();
    const [data, setData] = useState([]);
    const [project, setProject] = useState(null);
    const [glossary, setGlossary] = useState({ terms: [] });
    const [loading, setLoading] = useState(true);
    const [processing, setProcessing] = useState(false);
    const [isModalOpen, setIsModalOpen] = useState(false);
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

    const loadData = async () => {
        try {
            setLoading(true);
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
            setLoading(false);
        }
    };

    const handleEnhanceGlossary = async () => {
        setProcessing(true);
        try {
            // Pass the current episode name in a list to target only this episode
            await api.enhanceGlossary(projectName, { episode_names: [episodeName] });
            // Reload project data to get updated glossary
            const projRes = await api.getProject(projectName);
            setProject(projRes.data);
            setGlossary(projRes.data.glossary || { terms: [] });
            alert("Glossary enhanced with terms from this episode!");
        } catch (err) {
            console.error("Failed to enhance glossary", err);
            alert("Failed to enhance glossary");
        } finally {
            setProcessing(false);
        }
    };

    const handleConfirmTranslation = async (enhanceGlossary) => {
        setIsModalOpen(false);
        try {
            const settings = project?.settings || {};
            const translationModel = settings.translation_model || 'gemini-flash-latest';
            const res = await api.translateEpisode(projectName, episodeName, translationModel, enhanceGlossary);
            addJob(res.data.job_id, 'translate_episode', `Translating ${episodeName}`);
        } catch (err) {
            console.error("Failed to start translation", err);
            alert("Failed to start translation");
        }
    };

    if (loading) return <div className="h-screen flex items-center justify-center">Loading...</div>;

    return (
        <div className="h-screen flex flex-col bg-slate-50 dark:bg-gray-900">
            <TranslationModal
                isOpen={isModalOpen}
                onClose={() => setIsModalOpen(false)}
                onConfirm={handleConfirmTranslation}
                title={`Translate ${episodeName}`}
            />

            {/* Header */}
            <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 px-6 py-4 flex justify-between items-center shrink-0">
                <div className="flex items-center gap-4">
                    <Link to={`/project/${projectName}`} className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200">
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
                        disabled={processing}
                        className="flex items-center gap-2 px-4 py-2 bg-purple-100 text-purple-700 rounded-lg hover:bg-purple-200 transition-colors text-sm font-medium disabled:opacity-50"
                    >
                        <Sparkles size={18} />
                        Enhance Glossary
                    </button>
                    <button
                        onClick={() => setIsModalOpen(true)}
                        disabled={processing}
                        className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors text-sm font-medium disabled:opacity-50 shadow-lg shadow-indigo-500/20"
                    >
                        <Languages size={18} />
                        Translate Episode
                    </button>
                </div>
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
                />
            </div>
        </div>
    );
};

export default EpisodeView;
