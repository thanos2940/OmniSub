import { useRef } from 'react';
import { Download, FileJson, FileSpreadsheet } from 'lucide-react';
import { ensureUids, stripUids } from './useGlossaryState';

const GlossaryImportExport = ({
    glossary,
    terms,
    setTerms,
    suppressedTerms,
    projectName,
    isInternalUpdateRef,
    dirtyRef,
    toast,
}) => {
    const fileInputRef = useRef(null);
    const csvInputRef = useRef(null);

    const handleExportJSON = () => {
        const dataStr = JSON.stringify({
            ...glossary,
            terms: stripUids(terms),
            suppressed_terms: suppressedTerms
        }, null, 2);
        const blob = new Blob([dataStr], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `glossary_${projectName || glossary?.show_name || 'export'}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    const handleExportCSV = () => {
        const headers = ["term", "translation", "type", "gender", "case_sensitive", "keep_original", "description"];
        const rows = terms.map(t => [
            `"${(t.term || '').replace(/"/g, '""')}"`,
            `"${(t.translation || '').replace(/"/g, '""')}"`,
            `"${(t.type || 'other').replace(/"/g, '""')}"`,
            `"${(t.gender || 'n/a').replace(/"/g, '""')}"`,
            t.case_sensitive ? "true" : "false",
            t.keep_original ? "true" : "false",
            `"${(t.description || '').replace(/"/g, '""')}"`
        ]);

        const csvContent = [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
        const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `glossary_${projectName || 'export'}.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    const handleImportJSON = (e) => {
        const file = e.target.files[0];
        if (file) {
            const reader = new FileReader();
            reader.onload = (evt) => {
                try {
                    const parsed = JSON.parse(evt.target.result);
                    const imported = Array.isArray(parsed) ? parsed : (parsed.terms || []);
                    if (isInternalUpdateRef) isInternalUpdateRef.current = true;
                    if (dirtyRef) dirtyRef.current = true;
                    setTerms(prev => [...ensureUids(imported), ...prev]);
                    if (toast?.success) toast.success(`Imported ${imported.length} terms.`);
                } catch {
                    if (toast?.error) toast.error("Invalid JSON format.");
                }
            };
            reader.readAsText(file);
        }
        e.target.value = null;
    };

    const handleImportCSV = (e) => {
        const file = e.target.files[0];
        if (!file) return;

        const reader = new FileReader();
        reader.onload = (event) => {
            try {
                const text = event.target.result;
                const lines = text.split(/\r?\n/).filter(line => line.trim());
                if (lines.length <= 1) return;

                const headers = lines[0].split(',').map(h => h.trim().replace(/^"|"$/g, '').toLowerCase());
                const imported = [];

                for (let i = 1; i < lines.length; i++) {
                    const row = lines[i];
                    const values = row.match(/(".*?"|[^",\s]+)(?=\s*,|\s*$)/g) || row.split(',');
                    const cleanValues = values.map(v => v.trim().replace(/^"|"$/g, '').replace(/""/g, '"'));

                    const termObj = {};
                    headers.forEach((h, idx) => {
                        termObj[h] = cleanValues[idx] || '';
                    });

                    if (termObj.term && termObj.translation) {
                        imported.push({
                            term: termObj.term,
                            translation: termObj.translation,
                            type: termObj.type || 'other',
                            gender: termObj.gender || 'n/a',
                            case_sensitive: termObj.case_sensitive === 'true',
                            keep_original: termObj.keep_original === 'true',
                            description: termObj.description || '',
                        });
                    }
                }

                if (imported.length > 0) {
                    if (isInternalUpdateRef) isInternalUpdateRef.current = true;
                    if (dirtyRef) dirtyRef.current = true;
                    setTerms(prev => [...ensureUids(imported), ...prev]);
                    if (toast?.success) toast.success(`Imported ${imported.length} terms from CSV.`);
                }
            } catch (err) {
                console.error("CSV import error:", err);
                if (toast?.error) toast.error("Failed to parse CSV file.");
            }
        };
        reader.readAsText(file);
        e.target.value = null;
    };

    return (
        <div className="flex items-center gap-1">
            <input type="file" ref={fileInputRef} onChange={handleImportJSON} accept=".json" className="hidden" />
            <input type="file" ref={csvInputRef} onChange={handleImportCSV} accept=".csv" className="hidden" />

            <div className="flex items-center bg-gray-100 dark:bg-gray-700 rounded-xl p-0.5">
                <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="p-1.5 text-gray-600 dark:text-gray-300 hover:text-indigo-600 hover:bg-white dark:hover:bg-gray-800 rounded-lg transition-colors"
                    title="Import JSON"
                >
                    <FileJson className="w-4 h-4" />
                </button>
                <button
                    type="button"
                    onClick={() => csvInputRef.current?.click()}
                    className="p-1.5 text-gray-600 dark:text-gray-300 hover:text-indigo-600 hover:bg-white dark:hover:bg-gray-800 rounded-lg transition-colors"
                    title="Import CSV"
                >
                    <FileSpreadsheet className="w-4 h-4" />
                </button>
                <button
                    type="button"
                    onClick={handleExportJSON}
                    className="p-1.5 text-gray-600 dark:text-gray-300 hover:text-indigo-600 hover:bg-white dark:hover:bg-gray-800 rounded-lg transition-colors"
                    title="Export JSON"
                >
                    <Download className="w-4 h-4" />
                </button>
                <button
                    type="button"
                    onClick={handleExportCSV}
                    className="p-1.5 text-gray-600 dark:text-gray-300 hover:text-indigo-600 hover:bg-white dark:hover:bg-gray-800 rounded-lg transition-colors"
                    title="Export CSV"
                >
                    <FileSpreadsheet className="w-4 h-4 text-emerald-600" />
                </button>
            </div>
        </div>
    );
};

export default GlossaryImportExport;
