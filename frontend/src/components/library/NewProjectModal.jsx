import { useState } from 'react';
import { api } from '../../api';
import { useToast } from '../../context/ToastContext';
import { Button, Field, INPUT, Modal } from '../ui';

// Creates a project that is not synced from Sonarr/Radarr (for subtitle files you bring yourself).
export default function NewProjectModal({ open, onClose, onCreated, parents = [], defaultLanguage = 'Greek' }) {
    const toast = useToast();
    const [name, setName] = useState('');
    const [language, setLanguage] = useState(defaultLanguage);
    const [type, setType] = useState('show');
    const [parent, setParent] = useState('');
    const [saving, setSaving] = useState(false);

    const reset = () => { setName(''); setType('show'); setParent(''); };

    const submit = async (e) => {
        e.preventDefault();
        if (!name.trim()) return;
        setSaving(true);
        try {
            await api.createProject(name.trim(), language.trim() || defaultLanguage, parent || null, type);
            toast.success(`Created "${name.trim()}".`);
            reset();
            onCreated?.();
            onClose();
        } catch (err) {
            toast.error(`Could not create the project: ${err.response?.data?.detail || err.message}`);
        } finally {
            setSaving(false);
        }
    };

    return (
        <Modal
            open={open}
            title="New project"
            onClose={onClose}
            footer={(
                <>
                    <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
                    <Button variant="primary" type="submit" form="new-project-form" loading={saving}>Create</Button>
                </>
            )}
        >
            <form id="new-project-form" onSubmit={submit} className="space-y-4">
                <Field label="Name" htmlFor="np-name" hint="Shown in the library, and used as the folder name.">
                    <input id="np-name" className={INPUT} value={name} onChange={e => setName(e.target.value)} placeholder="Breaking Bad" required autoFocus />
                </Field>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <Field label="Translate into" htmlFor="np-lang">
                        <input id="np-lang" className={INPUT} value={language} onChange={e => setLanguage(e.target.value)} placeholder="Greek" />
                    </Field>
                    <Field label="Kind" htmlFor="np-type">
                        <select id="np-type" className={INPUT} value={type} onChange={e => setType(e.target.value)}>
                            <option value="show">Series</option>
                            <option value="movie">Movie</option>
                            <option value="parent">Parent (groups other projects)</option>
                        </select>
                    </Field>
                </div>
                {parents.length > 0 && (
                    <Field label="Belongs to" htmlFor="np-parent" hint="Optional. Shares context with the parent project.">
                        <select id="np-parent" className={INPUT} value={parent} onChange={e => setParent(e.target.value)}>
                            <option value="">Nothing</option>
                            {parents.map(p => <option key={p.name} value={p.name}>{p.show_name || p.name}</option>)}
                        </select>
                    </Field>
                )}
            </form>
        </Modal>
    );
}
