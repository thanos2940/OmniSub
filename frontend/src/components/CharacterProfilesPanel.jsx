import React, { useState, useEffect } from 'react';
import { FileText, Sparkles, Plus, Edit2, Trash2, X, Check, RefreshCw } from 'lucide-react';
import { api } from '../api';
import { useToast } from '../context/ToastContext';

const CharacterProfilesPanel = ({ projectName, projectSettings }) => {
    const [characters, setCharacters] = useState({});
    const [loading, setLoading] = useState(true);
    const [generating, setGenerating] = useState(false);
    const [editingChar, setEditingChar] = useState(null);
    const [formData, setFormData] = useState({ name: '', gender: 'unknown', formality: 'informal', speech_patterns: '', notes: '' });
    const toast = useToast();

    const loadCharacters = async () => {
        setLoading(true);
        try {
            const res = await api.getCharacters(projectName);
            setCharacters(res.data || {});
        } catch (err) {
            console.error("Failed to load characters", err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        loadCharacters();
    }, [projectName]);

    const handleGenerate = async () => {
        setGenerating(true);
        try {
            const model = projectSettings?.context_model || 'gemini-flash-latest';
            const res = await api.generateCharacters(projectName, model);
            const jobId = res.data?.job_id;
            if (!jobId) { toast.error("Failed to start generation."); return; }
            toast.success("Character profiles generation started...");
            // Poll until job completes (max ~30s)
            let attempts = 0;
            const poll = async () => {
                attempts++;
                const jobRes = await api.getJob(jobId);
                if (jobRes.data?.status === 'completed') {
                    toast.success("Character profiles generated!");
                    loadCharacters();
                } else if (jobRes.data?.status === 'failed') {
                    toast.error(`Profile generation failed: ${jobRes.data.message}`);
                } else if (attempts < 15) {
                    setTimeout(poll, 2000);
                }
            };
            setTimeout(poll, 2000);
        } catch (err) {
            console.error("Failed to generate characters", err);
            toast.error("Failed to generate characters.");
        } finally {
            setGenerating(false);
        }
    };

    const handleSave = async () => {
        if (!formData.name.trim()) return toast.error("Character name is required.");
        try {
            await api.updateCharacter(projectName, formData.name, formData);
            toast.success(`Character ${formData.name} saved.`);
            setEditingChar(null);
            loadCharacters();
        } catch (err) {
            console.error("Failed to save character", err);
            toast.error("Failed to save character.");
        }
    };

    const handleDelete = async (name) => {
        if (!window.confirm(`Delete profile for ${name}?`)) return;
        try {
            await api.deleteCharacter(projectName, name);
            toast.success("Character deleted.");
            loadCharacters();
        } catch (err) {
            console.error("Failed to delete character", err);
            toast.error("Failed to delete character.");
        }
    };

    const openEditModal = (charName = null) => {
        if (charName && characters[charName]) {
            setFormData({ ...characters[charName], name: charName });
            setEditingChar(charName);
        } else {
            setFormData({ name: '', gender: 'unknown', formality: 'informal', speech_patterns: '', notes: '' });
            setEditingChar('__new__');
        }
    };

    if (loading) {
        return (
            <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-8 text-center">
                <RefreshCw className="w-8 h-8 mx-auto text-emerald-400 animate-spin mb-4" />
                <p className="text-gray-500">Loading Character Profiles...</p>
            </div>
        );
    }

    const charsList = Object.keys(characters);

    return (
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden shadow-sm">
            <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50 flex justify-between items-center">
                <h3 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                    <FileText size={20} className="text-emerald-500" /> Character Profiles
                    <span className="text-sm font-normal text-gray-500 ml-2">({charsList.length} profiles)</span>
                </h3>
                <div className="flex items-center gap-2">
                    <button
                        onClick={handleGenerate}
                        disabled={generating}
                        className="flex items-center gap-1.5 text-indigo-600 hover:text-indigo-700 px-3 py-1.5 rounded-lg hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-colors text-sm font-medium disabled:opacity-50"
                    >
                        <Sparkles size={14} /> AI Generate
                    </button>
                    <button
                        onClick={() => openEditModal()}
                        className="flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium transition-colors"
                    >
                        <Plus size={14} /> Add Character
                    </button>
                    <button
                        onClick={loadCharacters}
                        className="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 rounded-lg transition-colors"
                        title="Refresh Profiles"
                    >
                        <RefreshCw size={16} />
                    </button>
                </div>
            </div>

            <div className="p-6">
                {charsList.length === 0 ? (
                    <div className="text-center py-8">
                        <FileText size={40} className="mx-auto text-gray-300 dark:text-gray-600 mb-3" />
                        <p className="text-gray-500">No character profiles found.</p>
                        <p className="text-sm text-gray-400 mt-1">Use AI Generate to extract profiles from the first episode.</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {charsList.map(name => {
                            const char = characters[name];
                            return (
                                <div key={name} className="bg-gray-50 dark:bg-gray-900/50 border border-gray-200 dark:border-gray-700 rounded-xl p-4 hover:border-emerald-300 transition-colors">
                                    <div className="flex justify-between items-start mb-2">
                                        <h4 className="font-bold text-gray-900 dark:text-white text-lg">{name}</h4>
                                        <div className="flex items-center gap-1">
                                            <button onClick={() => openEditModal(name)} className="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 rounded transition-colors" title="Edit">
                                                <Edit2 size={14} />
                                            </button>
                                            <button onClick={() => handleDelete(name)} className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-colors" title="Delete">
                                                <Trash2 size={14} />
                                            </button>
                                        </div>
                                    </div>
                                    <div className="flex gap-2 mb-3">
                                        <span className="px-2 py-0.5 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded text-xs text-gray-600 dark:text-gray-300 capitalize">
                                            Gender: {char.gender}
                                        </span>
                                        <span className="px-2 py-0.5 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded text-xs text-gray-600 dark:text-gray-300 capitalize">
                                            Formality: {char.formality}
                                        </span>
                                    </div>
                                    {char.speech_patterns && (
                                        <p className="text-sm text-gray-600 dark:text-gray-400 line-clamp-2 mb-2">
                                            <span className="font-semibold text-gray-700 dark:text-gray-300">Speech: </span>
                                            {char.speech_patterns}
                                        </p>
                                    )}
                                    {char.notes && (
                                        <p className="text-xs text-gray-500 italic line-clamp-1">{char.notes}</p>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>

            {editingChar && (
                <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
                    <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-xl w-full max-w-lg overflow-hidden">
                        <div className="p-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50 dark:bg-gray-800/50">
                            <h3 className="text-lg font-bold text-gray-900 dark:text-white">
                                {editingChar === '__new__' ? 'New Character Profile' : 'Edit Character Profile'}
                            </h3>
                            <button onClick={() => setEditingChar(null)} className="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-full transition-colors">
                                <X size={18} className="text-gray-500" />
                            </button>
                        </div>
                        <div className="p-6 space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Character Name</label>
                                <input
                                    type="text"
                                    value={formData.name}
                                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                                    disabled={editingChar !== '__new__'}
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 focus:ring-2 focus:ring-emerald-500 outline-none disabled:opacity-50"
                                />
                            </div>
                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Gender</label>
                                    <select
                                        value={formData.gender}
                                        onChange={(e) => setFormData({ ...formData, gender: e.target.value })}
                                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 focus:ring-2 focus:ring-emerald-500 outline-none"
                                    >
                                        <option value="unknown">Unknown</option>
                                        <option value="masculine">Masculine</option>
                                        <option value="feminine">Feminine</option>
                                        <option value="neuter">Neuter</option>
                                    </select>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Formality</label>
                                    <select
                                        value={formData.formality}
                                        onChange={(e) => setFormData({ ...formData, formality: e.target.value })}
                                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 focus:ring-2 focus:ring-emerald-500 outline-none"
                                    >
                                        <option value="informal">Informal</option>
                                        <option value="formal">Formal</option>
                                        <option value="mixed">Mixed</option>
                                    </select>
                                </div>
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Speech Patterns</label>
                                <textarea
                                    value={formData.speech_patterns}
                                    onChange={(e) => setFormData({ ...formData, speech_patterns: e.target.value })}
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 focus:ring-2 focus:ring-emerald-500 outline-none resize-y min-h-[80px]"
                                    placeholder="Describe their tone, humor, verbal tics..."
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Notes</label>
                                <input
                                    type="text"
                                    value={formData.notes}
                                    onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 focus:ring-2 focus:ring-emerald-500 outline-none"
                                    placeholder="Any additional info..."
                                />
                            </div>
                        </div>
                        <div className="p-4 border-t border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 flex justify-end gap-2">
                            <button onClick={() => setEditingChar(null)} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-200 dark:text-gray-300 dark:hover:bg-gray-700 rounded-lg transition-colors">
                                Cancel
                            </button>
                            <button onClick={handleSave} className="px-4 py-2 text-sm bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg flex items-center gap-2 transition-colors">
                                <Check size={16} /> Save Profile
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default CharacterProfilesPanel;
