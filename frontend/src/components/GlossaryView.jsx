import React from 'react';
import { motion } from 'framer-motion';
import { Book, Check, RefreshCw } from 'lucide-react';

const GlossaryView = ({ glossary, onConfirm, isLoading, isReRequest = false }) => {
    return (
        <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className={isReRequest ? "w-full" : "p-8 max-w-4xl mx-auto"}
        >
            <div className={`
                ${isReRequest ? '' : 'bg-white/40 backdrop-blur-xl rounded-3xl shadow-xl border border-white/50'} 
                overflow-hidden
            `}>
                {!isReRequest && (
                    <div className="p-6 border-b border-white/30 flex justify-between items-center">
                        <div className="flex items-center gap-3">
                            <Book className="w-6 h-6 text-indigo-600" />
                            <h2 className="text-xl font-bold text-gray-800">Spherical Context Glossary</h2>
                        </div>
                        <button
                            onClick={onConfirm}
                            disabled={isLoading}
                            className="flex items-center gap-2 px-6 py-2 bg-emerald-500 text-white rounded-lg font-semibold hover:bg-emerald-600 transition-colors disabled:opacity-50"
                        >
                            {isLoading ? 'Translating...' : (
                                <>
                                    <Check className="w-4 h-4" />
                                    Confirm & Translate
                                </>
                            )}
                        </button>
                    </div>
                )}

                <div className={`${isReRequest ? '' : 'p-6'} grid gap-4`}>
                    {/* Re-request Header if in modal */}
                    {isReRequest && (
                        <div className="flex justify-end mb-4">
                            <button
                                onClick={onConfirm}
                                disabled={isLoading}
                                className="flex items-center gap-2 px-6 py-2 bg-indigo-600 text-white rounded-lg font-semibold hover:bg-indigo-700 transition-colors disabled:opacity-50 shadow-md"
                            >
                                {isLoading ? 'Processing...' : (
                                    <>
                                        <RefreshCw className="w-4 h-4" />
                                        Update & Re-translate
                                    </>
                                )}
                            </button>
                        </div>
                    )}

                    {glossary.terms.length === 0 ? (
                        <p className="text-center text-gray-500 py-8">No terms detected yet.</p>
                    ) : (
                        glossary.terms.map((term, index) => (
                            <motion.div
                                key={index}
                                initial={{ opacity: 0, x: -20 }}
                                animate={{ opacity: 1, x: 0 }}
                                transition={{ delay: index * 0.05 }}
                                className="bg-white/60 border border-gray-100 p-4 rounded-xl flex flex-col md:flex-row gap-4 items-start md:items-center justify-between shadow-sm hover:shadow-md transition-shadow"
                            >
                                <div>
                                    <h3 className="font-bold text-gray-800">{term.term}</h3>
                                    <p className="text-sm text-gray-500">{term.description}</p>
                                </div>
                                <div className="flex gap-2">
                                    <span className="px-3 py-1 bg-indigo-100 text-indigo-700 rounded-full text-xs font-medium uppercase">
                                        {term.type}
                                    </span>
                                    <span className="px-3 py-1 bg-purple-100 text-purple-700 rounded-full text-xs font-medium uppercase">
                                        {term.gender}
                                    </span>
                                </div>
                            </motion.div>
                        ))
                    )}
                </div>
            </div>
        </motion.div>
    );
};

export default GlossaryView;
