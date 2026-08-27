import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { Lock, AlertCircle } from 'lucide-react';
import { api, AUTH_STORAGE_KEY } from '../api';

const LoginPage = ({ onLoggedIn }) => {
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!username.trim() || !password) {
            setError('Enter your username and password.');
            return;
        }
        setLoading(true);
        setError('');
        try {
            const res = await api.login(username.trim(), password);
            localStorage.setItem(AUTH_STORAGE_KEY, res.data.api_key);
            onLoggedIn?.();
        } catch (err) {
            if (err.response?.status === 429) {
                setError('Too many failed attempts. Wait a minute and try again.');
            } else {
                setError(err.response?.data?.detail || 'Invalid username or password.');
            }
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="min-h-screen flex items-center justify-center bg-slate-50 dark:bg-gray-900 p-4">
            <motion.div
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                className="w-full max-w-sm bg-white dark:bg-gray-800 rounded-2xl shadow-xl overflow-hidden"
            >
                <div className="bg-gradient-to-r from-indigo-500 to-purple-600 p-6 text-white text-center">
                    <div className="mx-auto w-12 h-12 rounded-xl bg-white/20 flex items-center justify-center mb-3">
                        <Lock size={22} />
                    </div>
                    <h1 className="text-xl font-bold">Omnisub</h1>
                    <p className="text-sm text-white/80">Sign in to continue</p>
                </div>

                <form onSubmit={handleSubmit} className="p-6 space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                            Username
                        </label>
                        <input
                            type="text"
                            autoFocus
                            value={username}
                            onChange={(e) => { setUsername(e.target.value); setError(''); }}
                            className="w-full px-4 py-2.5 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none text-sm"
                            autoComplete="username"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                            Password
                        </label>
                        <input
                            type="password"
                            value={password}
                            onChange={(e) => { setPassword(e.target.value); setError(''); }}
                            className="w-full px-4 py-2.5 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none text-sm"
                            autoComplete="current-password"
                        />
                    </div>

                    {error && (
                        <div className="flex items-start gap-2 text-sm text-red-600 dark:text-red-400">
                            <AlertCircle size={16} className="flex-shrink-0 mt-0.5" />
                            <span>{error}</span>
                        </div>
                    )}

                    <button
                        type="submit"
                        disabled={loading}
                        className="w-full px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                    >
                        {loading ? (
                            <>
                                <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                                Signing in...
                            </>
                        ) : (
                            'Sign in'
                        )}
                    </button>
                </form>
            </motion.div>
        </div>
    );
};

export default LoginPage;
