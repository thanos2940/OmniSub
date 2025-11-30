import React, { useCallback } from 'react';
import { Upload, FileText } from 'lucide-react';
import { motion } from 'framer-motion';

const FileUpload = ({ onUpload, isLoading }) => {
    const handleFileChange = (e) => {
        const file = e.target.files[0];
        if (file) {
            onUpload(file);
        }
    };

    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex flex-col items-center justify-center h-full p-8"
        >
            <div className="bg-white/40 backdrop-blur-xl p-12 rounded-3xl shadow-xl border border-white/50 text-center max-w-lg w-full">
                <div className="mb-6 flex justify-center">
                    <div className="p-4 bg-white/50 rounded-full">
                        <Upload className="w-12 h-12 text-indigo-600" />
                    </div>
                </div>
                <h2 className="text-2xl font-bold text-gray-800 mb-2">Upload Subtitle File</h2>
                <p className="text-gray-600 mb-8">Drag & drop your .srt file here, or click to select.</p>

                <label className="relative cursor-pointer group">
                    <input
                        type="file"
                        accept=".srt"
                        onChange={handleFileChange}
                        className="hidden"
                        disabled={isLoading}
                    />
                    <span className={`
            px-8 py-3 rounded-xl font-semibold text-white transition-all duration-300 shadow-lg
            ${isLoading ? 'bg-gray-400 cursor-not-allowed' : 'bg-indigo-600 hover:bg-indigo-700 hover:scale-105'}
          `}>
                        {isLoading ? 'Uploading...' : 'Select File'}
                    </span>
                </label>
            </div>
        </motion.div>
    );
};

export default FileUpload;
