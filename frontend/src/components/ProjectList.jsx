import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { Folder, Film, Tv, Plus, ChevronRight, ChevronDown, Globe, Book, FileText } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

const ProjectList = () => {
    const [projects, setProjects] = useState([]);
    const [projectDetails, setProjectDetails] = useState({});
    const [isCreating, setIsCreating] = useState(false);
    const [loading, setLoading] = useState(true);

    // Form State
    const [newProjectName, setNewProjectName] = useState('');
    const [targetLang, setTargetLang] = useState('English');
    const [parentProject, setParentProject] = useState('');
    const [projectType, setProjectType] = useState('show');
    const [showAdvanced, setShowAdvanced] = useState(false);

    // Search & Filter State
    const [searchQuery, setSearchQuery] = useState('');
    const [filterType, setFilterType] = useState('all');

    useEffect(() => { loadProjects(); }, []);

    const loadProjects = async () => {
        try {
            setLoading(true);
            const res = await api.getProjects();
            const fullProjects = res.data || [];
            
            const projectNames = fullProjects.map(p => p.name);
            const details = {};
            fullProjects.forEach(p => {
                details[p.name] = p;
            });

            setProjects(projectNames);
            setProjectDetails(details);
        } catch (err) {
            console.error("Failed to load projects", err);
        } finally {
            setLoading(false);
        }
    };

    const handleCreate = async (e) => {
        e.preventDefault();
        try {
            await api.createProject(newProjectName, targetLang, parentProject || null, projectType);
            setIsCreating(false);
            setNewProjectName('');
            setParentProject('');
            setProjectType('show');
            setShowAdvanced(false);
            loadProjects();
        } catch (err) {
            console.error("Failed to create project", err);
            alert("Failed to create project");
        }
    };

    // Filter projects based on search query and type
    const isFiltered = searchQuery !== '' || filterType !== 'all';

    const filteredProjects = projects.filter(name => {
        const detail = projectDetails[name];
        const showName = (detail?.show_name || name).toLowerCase();
        const lang = (detail?.target_language || '').toLowerCase();
        const matchesSearch = showName.includes(searchQuery.toLowerCase()) || lang.includes(searchQuery.toLowerCase());
        
        if (filterType === 'all') return matchesSearch;
        return matchesSearch && detail?.type === filterType;
    });

    // Group projects by hierarchy
    const rootProjects = projects.filter(name => !projectDetails[name]?.parent_project);
    const getSubprojects = (parentName) => projects.filter(name => projectDetails[name]?.parent_project === parentName);

    const ProjectCard = ({ name, meta, level = 0 }) => {
        const subprojects = isFiltered ? [] : getSubprojects(name);
        const isParent = meta?.type === 'parent';
        const isMovie = meta?.type === 'movie';
        const [isExpanded, setIsExpanded] = useState(true);

        const icon = isParent ? <Folder size={22} /> : (isMovie ? <Film size={22} /> : <Tv size={22} />);
        const colorClass = isParent
            ? 'bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400'
            : (isMovie
                ? 'bg-purple-100 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400'
                : 'bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400');

        const glossaryCount = meta?.glossary?.terms?.length || 0;
        const hasContext = !!meta?.context_guide;

        return (
            <div className="mb-3">
                <div className={`group relative bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 transition-all hover:shadow-md hover:border-gray-300 dark:hover:border-gray-600 ${level > 0 ? 'ml-8 border-l-4 border-l-indigo-500' : ''}`}>
                    <Link to={`/project/${encodeURIComponent(name)}`} className="block p-5">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-4">
                                <div className={`p-2.5 rounded-lg ${colorClass} transition-transform group-hover:scale-105`}>
                                    {icon}
                                </div>
                                <div>
                                    <h3 className="text-lg font-bold text-gray-900 dark:text-white group-hover:text-indigo-600 transition-colors">
                                        {meta?.show_name || name}
                                    </h3>
                                    <div className="flex items-center gap-3 mt-1">
                                        <span className="text-xs font-medium px-2 py-0.5 bg-gray-100 dark:bg-gray-700 rounded text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                                            {meta?.type || 'show'}
                                        </span>
                                        {meta?.target_language && (
                                            <span className="text-xs text-gray-400 flex items-center gap-1">
                                                <Globe size={10} /> {meta.target_language}
                                            </span>
                                        )}
                                        {glossaryCount > 0 && (
                                            <span className="text-xs text-gray-400 flex items-center gap-1">
                                                <Book size={10} /> {glossaryCount} terms
                                            </span>
                                        )}
                                        {hasContext && (
                                            <span className="text-xs text-green-500 flex items-center gap-1">
                                                <FileText size={10} /> Context ✓
                                            </span>
                                        )}
                                        {subprojects.length > 0 && (
                                            <span className="text-xs text-gray-400">
                                                • {subprojects.length} subprojects
                                            </span>
                                        )}
                                    </div>
                                </div>
                            </div>
                            <div className="flex items-center gap-2">
                                {subprojects.length > 0 && (
                                    <button
                                        onClick={(e) => { e.preventDefault(); setIsExpanded(!isExpanded); }}
                                        className="p-2 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-full transition-colors"
                                    >
                                        {isExpanded ? <ChevronDown size={20} className="text-gray-400" /> : <ChevronRight size={20} className="text-gray-400" />}
                                    </button>
                                )}
                            </div>
                        </div>
                    </Link>
                </div>

                <AnimatePresence>
                    {isExpanded && subprojects.length > 0 && (
                        <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
                            {subprojects.map(sub => (
                                <ProjectCard key={sub} name={sub} meta={projectDetails[sub]} level={level + 1} />
                            ))}
                        </motion.div>
                    )}
                </AnimatePresence>
            </div>
        );
    };

    return (
        <div className="p-8 max-w-5xl mx-auto">
            <div className="flex justify-between items-center mb-8">
                <div>
                    <h1 className="text-3xl font-bold text-gray-900 dark:text-white tracking-tight">Projects</h1>
                    <p className="text-gray-500 dark:text-gray-400 mt-1">Manage your subtitle translation projects.</p>
                </div>
                <button
                    onClick={() => setIsCreating(true)}
                    className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-5 py-2.5 rounded-xl transition-all shadow-sm hover:shadow-indigo-500/25 font-medium"
                >
                    <Plus size={20} />
                    New Project
                </button>
            </div>

            {/* Create Form */}
            <AnimatePresence>
                {isCreating && (
                    <motion.div
                        initial={{ opacity: 0, y: -20 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -20 }}
                        className="mb-8 bg-white dark:bg-gray-800 p-6 rounded-2xl shadow-lg border border-gray-200 dark:border-gray-700"
                    >
                        <h2 className="text-xl font-bold text-gray-900 dark:text-white mb-4">New Project</h2>
                        <form onSubmit={handleCreate} className="space-y-4">
                            {/* Simple Fields */}
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name</label>
                                    <input
                                        type="text"
                                        value={newProjectName}
                                        onChange={(e) => setNewProjectName(e.target.value)}
                                        className="w-full px-4 py-2.5 rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                        placeholder="e.g. Breaking Bad"
                                        required
                                        autoFocus
                                    />
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Target Language</label>
                                    <input
                                        type="text"
                                        value={targetLang}
                                        onChange={(e) => setTargetLang(e.target.value)}
                                        className="w-full px-4 py-2.5 rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                        placeholder="e.g. Greek, Spanish"
                                    />
                                </div>
                            </div>

                            {/* Advanced Toggle */}
                            <button
                                type="button"
                                onClick={() => setShowAdvanced(!showAdvanced)}
                                className="text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 flex items-center gap-1"
                            >
                                <ChevronRight size={14} className={`transition-transform ${showAdvanced ? 'rotate-90' : ''}`} />
                                Advanced Options
                            </button>

                            <AnimatePresence>
                                {showAdvanced && (
                                    <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
                                            <div>
                                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Type</label>
                                                <div className="grid grid-cols-3 gap-2">
                                                    {['show', 'movie', 'parent'].map(type => (
                                                        <button
                                                            key={type}
                                                            type="button"
                                                            onClick={() => setProjectType(type)}
                                                            className={`px-4 py-2 rounded-lg text-sm font-medium capitalize transition-colors ${projectType === type
                                                                ? 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/50 dark:text-indigo-300 border-2 border-indigo-500'
                                                                : 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300 border-2 border-transparent hover:bg-gray-200 dark:hover:bg-gray-600'
                                                                }`}
                                                        >
                                                            {type}
                                                        </button>
                                                    ))}
                                                </div>
                                            </div>
                                            <div>
                                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Parent Project</label>
                                                <select
                                                    value={parentProject}
                                                    onChange={(e) => setParentProject(e.target.value)}
                                                    className="w-full px-4 py-2.5 rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                                >
                                                    <option value="">None</option>
                                                    {projects.map(p => (
                                                        <option key={p} value={p}>{projectDetails[p]?.show_name || p}</option>
                                                    ))}
                                                </select>
                                            </div>
                                        </div>
                                    </motion.div>
                                )}
                            </AnimatePresence>

                            <div className="flex gap-3 justify-end pt-2">
                                <button type="button" onClick={() => { setIsCreating(false); setShowAdvanced(false); }} className="px-6 py-2.5 rounded-xl text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700 transition-colors font-medium">
                                    Cancel
                                </button>
                                <button type="submit" className="bg-indigo-600 hover:bg-indigo-700 text-white px-6 py-2.5 rounded-xl transition-all shadow-sm hover:shadow-indigo-500/25 font-medium">
                                    Create
                                </button>
                            </div>
                        </form>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Search & Filter Bar */}
            <div className="flex flex-col sm:flex-row gap-3 mb-6 bg-white dark:bg-gray-800 p-3 rounded-2xl border border-gray-200 dark:border-gray-700">
                <div className="flex-1 relative">
                    <input
                        type="text"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        placeholder="Search projects by name or language..."
                        className="w-full pl-4 pr-4 py-2.5 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 text-sm focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                    />
                </div>
                <div className="flex gap-2">
                    {[
                        { id: 'all', label: 'All' },
                        { id: 'show', label: 'Shows' },
                        { id: 'movie', label: 'Movies' },
                        { id: 'parent', label: 'Parents' }
                    ].map(btn => (
                        <button
                            key={btn.id}
                            type="button"
                            onClick={() => setFilterType(btn.id)}
                            className={`px-4 py-2 rounded-xl text-xs font-semibold capitalize transition-all ${
                                filterType === btn.id
                                    ? 'bg-indigo-600 text-white shadow-sm'
                                    : 'bg-gray-50 text-gray-600 dark:bg-gray-900 dark:text-gray-400 hover:bg-gray-150 dark:hover:bg-gray-850'
                            }`}
                        >
                            {btn.label}
                        </button>
                    ))}
                </div>
            </div>

            {/* Project List */}
            {loading ? (
                <div className="text-center py-20 text-gray-400">Loading projects...</div>
            ) : (
                <div className="space-y-1">
                    {isFiltered ? (
                        filteredProjects.map((project) => (
                            <ProjectCard key={project} name={project} meta={projectDetails[project]} />
                        ))
                    ) : (
                        rootProjects.map((project) => (
                            <ProjectCard key={project} name={project} meta={projectDetails[project]} />
                        ))
                    )}

                    {isFiltered && filteredProjects.length === 0 && (
                        <div className="text-center py-20 text-gray-500">
                            No projects matched your search.
                        </div>
                    )}

                    {projects.length === 0 && !isCreating && (
                        <div className="text-center py-20">
                            <div className="w-20 h-20 bg-gray-100 dark:bg-gray-800 rounded-full flex items-center justify-center mx-auto mb-4">
                                <Folder size={40} className="text-gray-400" />
                            </div>
                            <h3 className="text-xl font-bold text-gray-900 dark:text-white mb-2">No projects yet</h3>
                            <p className="text-gray-500 dark:text-gray-400 mb-6">Create your first project, or connect Sonarr/Radarr to import your existing library automatically.</p>
                            <div className="flex items-center justify-center gap-3">
                                <Link
                                    to="/settings"
                                    className="border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 px-6 py-2.5 rounded-xl transition-all font-medium"
                                >
                                    Connect Sonarr/Radarr
                                </Link>
                                <button
                                    onClick={() => setIsCreating(true)}
                                    className="bg-indigo-600 hover:bg-indigo-700 text-white px-6 py-2.5 rounded-xl transition-all shadow-sm hover:shadow-indigo-500/25 font-medium"
                                >
                                    Create Project
                                </button>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export default ProjectList;
